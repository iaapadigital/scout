# /opt — Midway Scout

Project root for **Midway Scout**, a self-hosted agent-dispatch service that scrapes
amusement/attractions industry news into newsletter-ready Markdown digests.

```
/opt
├── midway-scout/       the application (extracted from the SFTP upload)
└── sftp_uploads/       drop zone for uploads (root:sftpusers) — source zips live here
```

## Layout

```
midway-scout/
├── app/
│   ├── main.py           FastAPI app: dashboard route + REST API
│   ├── orchestrator.py   fan-out: one agent per topic, asyncio.Semaphore-capped
│   ├── config.py         ALL config; env-driven via .env (single source of truth)
│   ├── llm.py            async LLM client: openai | anthropic | ollama | claude-code
│   ├── db.py             SQLite job store (midway.db, created on startup)
│   ├── textutil.py       keyword scoring + extractive summaries (keyless mode)
│   └── agents/
│       ├── rss_agent.py        keyless: trade-press RSS + Bing News → trafilatura
│       ├── firecrawl_agent.py  Firecrawl /v1/search → LLM distill
│       └── browser_agent.py    browser-use + headless Chromium (heaviest)
├── static/index.html     single-file dashboard, no build step
├── outputs/              digest-<job_id>.md files
├── setup.sh / run.sh     venv bootstrap / uvicorn launcher
└── midway-scout.service  optional systemd unit
```

## Running it

```bash
cd /opt/midway-scout
./setup.sh          # apt deps + .venv + pip install + playwright chromium
./run.sh            # uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Dashboard at `http://<host>:8000`. Requires port 8000/tcp open, or tunnel with
`ssh -L 8000:localhost:8000 ubuntu@<ip>`.

Every agent contract is the same signature — keep it that way when adding engines:

```python
async def run(topic: str, context: str, job_id: str, log) -> list[dict]
```

Each returned dict must carry: `topic, engine, title, url, summary, why_it_matters,
published_hint`. `orchestrator._build_digest` reads exactly those keys. Register a new
engine in `orchestrator.ENGINES`-adjacent dict, `_pick_engine`, and the `engine` pattern
in `main.JobRequest`.

## This box's constraints (checked 2026-08-06)

- **Python 3.14.4 is the only interpreter** (no 3.11/3.12, and neither is in apt), so
  `setup.sh`'s "prefer 3.12" line always falls through to `python3`. That's fine —
  verified by `pip install --dry-run --only-binary=:all: -r requirements.txt`: the whole
  set resolves to 3.14 wheels, including `browser-use 0.13.7`, `lxml 6.1.1`, and
  `trafilatura 2.2.0`. No source builds. `python3-venv`/`python3-pip` are installed.
- **1 GB RAM.** Headless Chromium (`browser` engine) and Ollama (~8 GB) are not viable
  here. Ship with the **rss** engine. `setup.sh` now auto-skips the Chromium download
  below 3.5 GB, so it won't waste the disk.
- Outbound HTTPS works (pypi.org and blooloop.com/feed both 200), so the RSS engine's
  feed fetches are unblocked.
- **`claude` CLI is installed** (`/home/ubuntu/.local/bin/claude`), so
  `LLM_PROVIDER=claude-code` is the zero-key path to real summaries — it shells out to
  `claude -p`, capped at 3 concurrent calls in `llm.py`.
- `/opt` itself is `root:root` and not writable by `ubuntu`; `/opt/midway-scout` is
  owned by `ubuntu`. Creating files at the `/opt` level needs `sudo`.

## Conventions

- **Config only via `app/config.py`.** Never read `os.getenv` elsewhere (the one
  exception, `RSS_FEEDS` in `rss_agent._feed_list`, is a wart — fold it into config if
  you touch it).
- Agent failures are per-topic and non-fatal: `orchestrator.scout` catches, logs to the
  job progress feed, and returns `[]`. Preserve that — one dead feed must not fail a run.
- LLM output is parsed with `llm.extract_json` (tolerant of fences/prose). Always have a
  non-LLM fallback path, as `rss_agent._summarize` does.
- Cost levers live in `.env`: `ARTICLES_PER_TOPIC` (≈ one LLM call each),
  `MAX_CONTENT_CHARS`, and the 350-token summary cap in the agents.

## Version history

Upstream arrives as `midway-scout (N).zip` in `/opt/sftp_uploads/` and is unzipped over
this tree. **v2 is deployed** (2026-08-06). What v2 changed:

- `midway-scout.service` paths corrected to `/opt/midway-scout` + a note that systemd's
  PATH excludes `~/.local/bin` (set an absolute `CLAUDE_CODE_BIN` if using claude-code).
- `db.fail_stale_jobs()`, called from startup, flips orphaned `queued`/`running` rows to
  `failed` instead of leaving them hung forever.
- `firecrawl_agent._search` tries `/v2/search` then falls back to `/v1/search` on
  404/410, and unwraps v2's `data.web` nesting; logs a clear message on 0 results.
- `rss_agent.run` explains an empty result (all feeds down = no outbound HTTPS; only
  Bing down = Bing RSS retired).
- `setup.sh` prefers python3.12/3.11 and skips the Chromium download under 3.5 GB RAM.
- `config.py` model defaults now match `.env.example`/README (`gpt-4.1-nano`,
  `claude-3-5-haiku-latest`).

### Local patches (re-apply after the next zip drop)

- `setup.sh`: `DEBIAN_FRONTEND=noninteractive` + `sudo -E` + `--force-confold` on the
  apt lines. Without it, apt stops on a debconf dialog (this box has a pending-kernel
  prompt) and `set -e` kills the script. Worth pushing upstream.

## Known gaps (still unfixed, in rough priority order)

1. **No auth on the API.** Anyone reaching port 8000 can dispatch agents and spend
   credits. Restrict the security group / add nginx basic auth before sharing a URL.
   Untouched by v2 and the single biggest thing standing between this and a shared URL.
2. **Restart still loses in-flight work.** v2 reports the loss instead of hiding it, but
   there's no requeue and `main.create_job` still uses the deprecated
   `asyncio.get_event_loop().create_task(...)` (use `asyncio.create_task`).
3. **Anthropic default is a generation behind.** v2 settled the drift on
   `claude-3-5-haiku-latest`; the current cheapest tier is Haiku 4.5
   (`claude-haiku-4-5-20251001`). One-line change in `.env` when you want it.
4. **Bing News RSS is diagnosed, not replaced.** If it has been retired, the curated
   trade-press feeds still carry the engine — but consider a real search fallback.
5. `@app.on_event("startup")` is deprecated FastAPI; migrate to a lifespan handler.
6. `db.py` calls are sync sqlite3 on the event loop, and `requirements.txt` pulls the
   heavy `browser-use` stack even for keyless RSS use. Both fine at one-team scale;
   revisit if either bites.
