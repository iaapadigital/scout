"""Firecrawl-powered scout.

Uses Firecrawl's /v1/search endpoint (search the web AND scrape each hit to
markdown in one call), then distills each hit with the cheap LLM into a
newsletter-ready blurb. Direct HTTP so we don't chase SDK versions."""
import asyncio

import httpx

from .. import config, llm

TIMEOUT = httpx.Timeout(180.0, connect=15.0)

DISTILL_SYSTEM = (
    "You are a research assistant for a newsletter covering the amusement, "
    "attractions, and entertainment industry (theme parks, FECs, arcades, "
    "water parks, IAAPA-world vendors, location-based entertainment). "
    "You turn raw scraped articles into tight newsletter blurbs."
)

DISTILL_PROMPT = """Topic being researched: {topic}
Extra context from the editor: {context}

Below is a scraped article. Respond ONLY with a JSON object, no markdown fences:
{{"relevant": true/false, "title": "...", "summary": "2-3 sentence factual summary",
"why_it_matters": "1 sentence on why a reader in the amusement/entertainment industry cares",
"published_hint": "date if visible in the text, else empty string"}}

Mark relevant=false if the page is a homepage, category page, paywall stub, or unrelated to the topic.

URL: {url}
TITLE: {title}
CONTENT:
{content}
"""


async def _search(query: str, limit: int) -> list[dict]:
    """Try /v2/search first, fall back to /v1/search (Firecrawl has both;
    which one your key targets depends on account/plan vintage)."""
    body = {
        "query": query,
        "limit": limit,
        "scrapeOptions": {"formats": ["markdown"], "onlyMainContent": True},
    }
    headers = {"Authorization": f"Bearer {config.FIRECRAWL_API_KEY}"}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        last_err = None
        for version in ("v2", "v1"):
            try:
                r = await client.post(f"{config.FIRECRAWL_BASE_URL}/{version}/search",
                                      headers=headers, json=body)
                r.raise_for_status()
                data = r.json().get("data", []) or []
                # v2 nests results under data.web
                if isinstance(data, dict):
                    data = data.get("web", []) or []
                return data
            except httpx.HTTPStatusError as e:
                last_err = e
                if e.response.status_code in (404, 410):
                    continue  # endpoint version not available, try the other
                raise
    raise last_err


async def run(topic: str, context: str, job_id: str, log) -> list[dict]:
    """Return a list of article dicts for one topic."""
    query = f"{topic} amusement entertainment industry news"
    log(f"[firecrawl] searching: {query}")
    hits = await _search(query, config.ARTICLES_PER_TOPIC + 3)
    if not hits:
        log(f"[firecrawl] '{topic}': search returned 0 results — verify your "
            f"FIRECRAWL_API_KEY and plan at firecrawl.dev, or switch engine to rss")
        return []
    log(f"[firecrawl] {len(hits)} pages fetched for '{topic}', distilling…")

    async def distill(hit: dict) -> dict | None:
        content = (hit.get("markdown") or hit.get("description") or "")[: config.MAX_CONTENT_CHARS]
        if not content:
            return None
        try:
            raw = await llm.complete(
                DISTILL_PROMPT.format(
                    topic=topic,
                    context=context or "none",
                    url=hit.get("url", ""),
                    title=hit.get("title", ""),
                    content=content,
                ),
                system=DISTILL_SYSTEM,
                max_tokens=350,
            )
        except Exception as e:  # LLM hiccup on one article shouldn't kill the topic
            log(f"[firecrawl] distill error on {hit.get('url','?')}: {e}")
            return None
        parsed = llm.extract_json(raw)
        if not parsed or not parsed.get("relevant"):
            return None
        return {
            "topic": topic,
            "engine": "firecrawl",
            "title": parsed.get("title") or hit.get("title", "Untitled"),
            "url": hit.get("url", ""),
            "summary": parsed.get("summary", ""),
            "why_it_matters": parsed.get("why_it_matters", ""),
            "published_hint": parsed.get("published_hint", ""),
        }

    distilled = await asyncio.gather(*(distill(h) for h in hits))
    articles = [a for a in distilled if a][: config.ARTICLES_PER_TOPIC]
    log(f"[firecrawl] '{topic}': {len(articles)} relevant articles kept")
    return articles
