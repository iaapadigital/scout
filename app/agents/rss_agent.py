"""RSS scout — the zero-key engine.

Sources:
  1. Curated amusement/attractions trade press RSS feeds (override with the
     RSS_FEEDS env var, comma-separated URLs).
  2. Bing News RSS search (free, keyless, returns direct article URLs).

Pipeline: gather feed entries -> score by topic keyword overlap -> fetch the
top candidates and extract full text with trafilatura -> summarize with the
LLM if one is configured, otherwise extractive lead-sentence summaries."""
import asyncio
import urllib.parse

import feedparser
import httpx
import trafilatura

from .. import config, llm, textutil

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) MidwayScout/1.0 (+newsletter research)"}
TIMEOUT = httpx.Timeout(30.0, connect=10.0)

CURATED_FEEDS = [
    ("Blooloop", "https://blooloop.com/feed/"),
    ("InPark Magazine", "https://www.inparkmagazine.com/feed/"),
    ("Attractions Magazine", "https://attractionsmagazine.com/feed/"),
    ("Coaster101", "https://www.coaster101.com/feed/"),
    ("Park World Online", "https://www.parkworld-online.com/feed/"),
    ("Amusement Today", "https://amusementtoday.com/feed/"),
]

BING_NEWS_RSS = "https://www.bing.com/news/search?q={q}&format=RSS"

DISTILL_SYSTEM = (
    "You are a research assistant for a newsletter covering the amusement, "
    "attractions, and entertainment industry. You turn raw articles into "
    "tight newsletter blurbs."
)
DISTILL_PROMPT = """Topic: {topic}
Editor context: {context}

Respond ONLY with JSON (no fences):
{{"relevant": true/false, "title": "...", "summary": "2-3 sentence factual summary",
"why_it_matters": "1 sentence for an amusement/entertainment industry reader",
"published_hint": "date if visible, else empty"}}

URL: {url}
TITLE: {title}
CONTENT:
{content}
"""


def _feed_list() -> list[tuple[str, str]]:
    import os
    override = os.getenv("RSS_FEEDS", "").strip()
    if override:
        return [("custom", u.strip()) for u in override.split(",") if u.strip()]
    return CURATED_FEEDS


async def _fetch(client: httpx.AsyncClient, url: str) -> str:
    r = await client.get(url, headers=UA, follow_redirects=True)
    r.raise_for_status()
    return r.text


async def _gather_candidates(topic: str, log) -> list[dict]:
    sources = _feed_list() + [
        ("Bing News", BING_NEWS_RSS.format(
            q=urllib.parse.quote_plus(f"{topic} amusement entertainment")))
    ]
    candidates: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        async def pull(name: str, url: str):
            try:
                raw = await _fetch(client, url)
            except Exception as e:
                log(f"[rss] feed unavailable ({name}): {type(e).__name__}")
                return
            parsed = feedparser.parse(raw)
            for e in parsed.entries[:40]:
                link = getattr(e, "link", "")
                if not link or link in candidates:
                    continue
                candidates[link] = {
                    "title": getattr(e, "title", "Untitled"),
                    "url": link,
                    "snippet": getattr(e, "summary", "")[:500],
                    "published": getattr(e, "published", ""),
                    "source": name,
                }
        await asyncio.gather(*(pull(n, u) for n, u in sources))
    items = list(candidates.values())
    for it in items:
        it["score"] = textutil.keyword_score(topic, f"{it['title']} {it['snippet']}")
    items = [i for i in items if i["score"] > 0]
    items.sort(key=lambda i: i["score"], reverse=True)
    log(f"[rss] '{topic}': {len(items)} matching entries across feeds")
    return items[: config.ARTICLES_PER_TOPIC * 2]


async def _article_text(client: httpx.AsyncClient, url: str) -> str:
    try:
        html = (await client.get(url, headers=UA, follow_redirects=True)).text
        return trafilatura.extract(html, include_comments=False) or ""
    except Exception:
        return ""


async def _summarize(topic: str, context: str, cand: dict, text: str, log) -> dict | None:
    content = (text or cand["snippet"] or "").strip()
    if not content:
        return None
    base = {
        "topic": topic,
        "engine": "rss",
        "title": cand["title"],
        "url": cand["url"],
        "published_hint": cand.get("published", ""),
        "source": cand.get("source", ""),
    }
    if not config.llm_ready():  # extractive mode — fully keyless
        return {**base,
                "summary": textutil.extractive_summary(content),
                "why_it_matters": ""}
    try:
        raw = await llm.complete(
            DISTILL_PROMPT.format(topic=topic, context=context or "none",
                                  url=cand["url"], title=cand["title"],
                                  content=content[: config.MAX_CONTENT_CHARS]),
            system=DISTILL_SYSTEM, max_tokens=350)
        parsed = llm.extract_json(raw)
    except Exception as e:
        log(f"[rss] LLM distill failed on {cand['url']}: {e} — using extractive")
        parsed = None
    if parsed is None:
        return {**base, "summary": textutil.extractive_summary(content),
                "why_it_matters": ""}
    if not parsed.get("relevant"):
        return None
    return {**base,
            "title": parsed.get("title") or cand["title"],
            "summary": parsed.get("summary", ""),
            "why_it_matters": parsed.get("why_it_matters", ""),
            "published_hint": parsed.get("published_hint") or cand.get("published", "")}


async def run(topic: str, context: str, job_id: str, log) -> list[dict]:
    log(f"[rss] scouting '{topic}' via trade press feeds + Bing News (keyless)")
    cands = await _gather_candidates(topic, log)
    if not cands:
        log(f"[rss] '{topic}': no matching entries — if ALL feeds show unavailable "
            f"above, the box may lack outbound HTTPS; if only Bing failed, the "
            f"curated feeds still work and Bing RSS may have been retired. "
            f"Try broader topic keywords or set RSS_FEEDS in .env.")
        return []
    articles: list[dict] = []
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        texts = await asyncio.gather(*(_article_text(client, c["url"]) for c in cands))
    for cand, text in zip(cands, texts):
        if len(articles) >= config.ARTICLES_PER_TOPIC:
            break
        a = await _summarize(topic, context, cand, text, log)
        if a:
            articles.append(a)
    log(f"[rss] '{topic}': {len(articles)} articles kept")
    return articles
