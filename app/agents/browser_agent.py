"""browser-use powered scout.

Drives a real headless Chromium via the browser-use Agent, for sources that
plain HTTP scraping can't handle (JS-heavy sites, interactive search).
Slower and pricier per article than Firecrawl, so it's the fallback / opt-in
engine. browser-use has moved its LLM wrappers between versions, so imports
are shimmed."""
import json

from .. import config, llm

TASK_TEMPLATE = """You are researching for a newsletter about the amusement and
entertainment industry (theme parks, attractions, FECs, arcades, water parks,
location-based entertainment).

Topic: {topic}
Extra editor context: {context}

Steps:
1. Go to a news search engine (Google News or Bing News) and search for recent
   news about the topic within the amusement/entertainment industry.
2. Open the {n} most promising, recent, substantive articles (skip homepages,
   category pages, and paywalled stubs).
3. For each article capture: title, url, a 2-3 sentence factual summary, and one
   sentence on why it matters to someone in this industry.

When finished, return ONLY a JSON array (no prose, no markdown fences):
[{{"title": "...", "url": "...", "summary": "...", "why_it_matters": "...", "published_hint": ""}}]
"""


def _make_llm():
    """Return a browser-use-compatible chat model across library versions."""
    if config.LLM_PROVIDER == "anthropic":
        try:  # browser-use >= 0.3 native wrappers
            from browser_use.llm import ChatAnthropic
            return ChatAnthropic(model=config.ANTHROPIC_MODEL, api_key=config.ANTHROPIC_API_KEY)
        except ImportError:
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(model=config.ANTHROPIC_MODEL, api_key=config.ANTHROPIC_API_KEY)
    try:
        from browser_use.llm import ChatOpenAI
        return ChatOpenAI(model=config.OPENAI_MODEL, api_key=config.OPENAI_API_KEY)
    except ImportError:
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=config.OPENAI_MODEL, api_key=config.OPENAI_API_KEY)


async def run(topic: str, context: str, job_id: str, log) -> list[dict]:
    from browser_use import Agent  # imported lazily: heavy, and optional at runtime

    task = TASK_TEMPLATE.format(topic=topic, context=context or "none", n=config.ARTICLES_PER_TOPIC)
    log(f"[browser] launching agent for '{topic}'")

    agent = Agent(task=task, llm=_make_llm())
    history = await agent.run(max_steps=config.BROWSER_AGENT_MAX_STEPS)

    # final_result() exists on modern versions; fall back to last extracted text
    final = ""
    if hasattr(history, "final_result"):
        final = history.final_result() or ""
    if not final and hasattr(history, "extracted_content"):
        chunks = history.extracted_content() or []
        final = chunks[-1] if chunks else ""

    parsed = llm.extract_json(final) if final else None
    if not isinstance(parsed, list):
        log(f"[browser] '{topic}': agent returned unstructured output, salvaging via LLM")
        try:
            raw = await llm.complete(
                "Convert the following research notes into a JSON array of "
                '{"title","url","summary","why_it_matters","published_hint"} objects. '
                "Respond with ONLY the JSON array.\n\n" + str(final)[:8000]
            )
            parsed = llm.extract_json(raw)
        except Exception:
            parsed = None
    if not isinstance(parsed, list):
        log(f"[browser] '{topic}': could not recover structured results")
        return []

    articles = []
    for item in parsed[: config.ARTICLES_PER_TOPIC]:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        articles.append(
            {
                "topic": topic,
                "engine": "browser-use",
                "title": item.get("title", "Untitled"),
                "url": item.get("url", ""),
                "summary": item.get("summary", ""),
                "why_it_matters": item.get("why_it_matters", ""),
                "published_hint": item.get("published_hint", ""),
            }
        )
    log(f"[browser] '{topic}': {len(articles)} articles captured")
    return articles
