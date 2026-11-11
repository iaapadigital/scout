"""Fan-out orchestrator.

One job = one editorial request. Each topic in the job gets its own agent,
run concurrently up to the job's agent budget. Results merge into a single
newsletter digest (markdown) saved to disk and the DB."""
import asyncio
import time
import traceback

from . import config, db
from .agents import browser_agent, firecrawl_agent, rss_agent


ENGINES = {}  # filled below


def _pick_engine(requested: str) -> str:
    if requested in ("firecrawl", "browser", "rss"):
        return requested
    # auto: firecrawl if keyed (best quality/speed), else keyless RSS mode
    return "firecrawl" if config.firecrawl_ready() else "rss"


async def run_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        return
    db.set_status(job_id, "running")

    def log(msg: str):
        db.log_progress(job_id, msg)

    engine = _pick_engine(job["engine"])
    log(f"dispatch: {len(job['topics'])} topic(s), engine={engine}, "
        f"max {job['num_agents']} concurrent agent(s)")

    sem = asyncio.Semaphore(max(1, job["num_agents"]))
    runner = {"firecrawl": firecrawl_agent.run,
              "browser": browser_agent.run,
              "rss": rss_agent.run}[engine]

    async def scout(topic: str) -> list[dict]:
        async with sem:
            try:
                return await runner(topic, job["context"], job_id, log)
            except Exception as e:
                log(f"agent for '{topic}' failed: {e}")
                traceback.print_exc()
                return []

    try:
        batches = await asyncio.gather(*(scout(t) for t in job["topics"]))
        articles = [a for batch in batches for a in batch]
        digest = _build_digest(job, articles, engine)
        db.save_results(job_id, articles, digest)

        out_path = config.OUTPUT_DIR / f"digest-{job_id}.md"
        out_path.write_text(digest, encoding="utf-8")
        log(f"digest saved: {out_path.name} ({len(articles)} articles)")
        db.set_status(job_id, "done" if articles else "failed",
                      "" if articles else "No relevant articles found. Check API keys and progress log.")
    except Exception as e:
        traceback.print_exc()
        db.set_status(job_id, "failed", str(e))


def _build_digest(job: dict, articles: list[dict], engine: str) -> str:
    date = time.strftime("%B %d, %Y")
    lines = [
        f"# Industry Scout Digest — {date}",
        "",
        f"*Topics: {', '.join(job['topics'])}*  ",
        f"*Engine: {engine} · {len(articles)} articles*",
        "",
    ]
    if job["context"]:
        lines += [f"> Editor context: {job['context']}", ""]
    for topic in job["topics"]:
        topic_articles = [a for a in articles if a["topic"] == topic]
        lines += [f"## {topic}", ""]
        if not topic_articles:
            lines += ["_No relevant articles found for this topic._", ""]
            continue
        for a in topic_articles:
            pub = f" · {a['published_hint']}" if a.get("published_hint") else ""
            lines += [
                f"### [{a['title']}]({a['url']})",
                f"{a['summary']}",
                "",
                f"**Why it matters:** {a['why_it_matters']}{pub}",
                "",
            ]
    return "\n".join(lines)
