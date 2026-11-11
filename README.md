# Midway Scout

A self-hosted agent-dispatch service for scraping amusement & entertainment
industry news into newsletter-ready digests. Built for Evan's newsletter
workflow: pick topics, add editor context, dispatch as many scout agents as
needed, get back a Markdown digest with per-article summaries and
"why it matters" lines.

## Zero keys? Zero problem.

The default configuration (`LLM_PROVIDER=none`, no Firecrawl key) works with
**no API keys at all**. The RSS engine pulls from the industry's trade press
feeds (Blooloop, InPark Magazine, Attractions Magazine, Coaster101, Park
World, Amusement Today — override with `RSS_FEEDS` in `.env`) plus Bing News
RSS search (free, keyless), extracts full article text locally, ranks by
topic-keyword relevance, and writes extractive summaries. Upgrade paths, all
one-line `.env` changes:

| Mode | Keys | What you gain |
|---|---|---|
| RSS + extractive (default) | none | works today, $0 |
| RSS/Firecrawl + **claude-code** | none — your Claude Max login | real Claude-written summaries using the `claude` CLI already on the box (`claude` once to log in, then `LLM_PROVIDER=claude-code`). Uses your personal plan limits, so best kept internal, not team-wide |
| RSS/Firecrawl + **Ollama** | none | real AI summaries via a free local LLM (needs ~8GB RAM: `curl -fsSL https://ollama.com/install.sh | sh && ollama pull llama3.2`) |
| + Firecrawl key | firecrawl.dev free tier | web-wide search beyond the feed list |
| + OpenAI/Anthropic key | paid | best summaries + enables the browser agent |

## What's inside

- **Web dashboard + REST API** (FastAPI) — dispatch runs, watch live agent
  progress, copy/download the digest.
- **Three scraping engines behind one interface:**
  - **RSS + trade press** — keyless. Curated industry feeds + Bing News RSS,
    local text extraction, keyword-ranked, extractive or LLM summaries.
  - **Firecrawl (cloud API)** — search-the-web + scrape-to-markdown in one
    call. Fast and cheap per article. Primary engine.
  - **browser-use** — a real headless Chromium driven by an LLM, for
    JS-heavy sites and interactive research. Slower/pricier, so it's the
    fallback and the opt-in choice.
  - `auto` mode uses Firecrawl when a key is present, keyless RSS otherwise.
- **Fan-out orchestration** — one agent per topic, concurrency capped per run.
- **LLM distillation** — every scraped page passes through a cheap model
  (default `gpt-4o-mini`; flip one env var for Claude Haiku) that filters
  irrelevant pages and writes the blurbs.
- **SQLite job store + Markdown digests** saved to `outputs/`.

## Quick start (fresh Ubuntu EC2)

```bash
# 1. Upload and unzip
unzip midway-scout.zip && cd midway-scout

# 2. Install everything (python venv, deps, headless Chromium)
chmod +x setup.sh run.sh
./setup.sh

# 3. (Optional) add keys — it runs keyless out of the box
nano .env

# 4. Run
./run.sh
```

Open `http://<EC2-public-IP>:8000`. You'll need port **8000/tcp** open in the
instance's security group (or tunnel: `ssh -L 8000:localhost:8000 ubuntu@<ip>`).

To keep it running after you log out, see `midway-scout.service` (systemd).

## API keys you need

| Key | Where | Notes |
|---|---|---|
| `OPENAI_API_KEY` | platform.openai.com | cheapest capable paid API (`gpt-4o-mini`) |
| `ANTHROPIC_API_KEY` | console.anthropic.com | optional alternative (`LLM_PROVIDER=anthropic`) |
| `FIRECRAWL_API_KEY` | firecrawl.dev | free tier ≈ 500 credits; recommended |

Why Firecrawl cloud instead of self-hosting: the self-hosted stack needs
Docker + Redis + several workers and doesn't include their managed search —
on a small EC2 box the cloud API is the pragmatic choice. If usage grows,
self-hosting is a drop-in later (`FIRECRAWL_BASE_URL` in `.env`).

### Note on ChatGPT Plus/Pro
Consumer ChatGPT subscriptions don't include API access, and session-token
workarounds violate OpenAI's terms — so there's no supported way to power
this with a ChatGPT plan. The Claude Max route above works because headless
mode (`claude -p`) is an official Claude Code feature.

## Keeping usage low

Defaults are pinned to the cheapest tier of every provider and are safe to
leave alone:

| Provider | Default model | Why |
|---|---|---|
| claude-code | `haiku` (via `CLAUDE_CODE_MODEL`) | cheapest tier against your Max plan limits |
| openai | `gpt-4.1-nano` | OpenAI's cheapest ($0.10/M input) |
| anthropic | `claude-3-5-haiku-latest` | budget tier; `claude-3-haiku-20240307` is cheaper still |
| ollama | `llama3.2` (3B) | free; `llama3.2:1b` for tiny instances |

Cost levers in `.env`: `ARTICLES_PER_TOPIC` (each article = one LLM call),
`MAX_CONTENT_CHARS` (input tokens per call, default ~1k), and summary output
is capped at 350 tokens in code. Ballpark: a 3-topic run ≈ 15 LLM calls ≈
~20k tokens total — fractions of a cent on nano/haiku, and a light dent in a
Max plan via claude-code.

## Using it

**Dashboard:** enter topics (one per line = one agent each), optional editor
context ("last 30 days, North America, prefer trade press"), pick an engine
and concurrency, hit **Dispatch Agents**. Each run prints a "ticket" — click
it for the live progress log, per-topic results, and the digest.

**API (for wiring into Evan's tool):**

```bash
# dispatch
curl -X POST http://localhost:8000/api/jobs \
  -H 'Content-Type: application/json' \
  -d '{"topics":["new roller coaster announcements","FEC acquisitions"],
       "context":"last 30 days, prefer trade press",
       "engine":"auto","num_agents":3}'
# → {"job_id":"a1b2c3d4e5f6","status":"queued"}

# poll
curl http://localhost:8000/api/jobs/a1b2c3d4e5f6

# fetch the digest
curl http://localhost:8000/api/jobs/a1b2c3d4e5f6/digest.md
```

## Architecture

```
static/index.html      dashboard (single file, no build step)
app/main.py            FastAPI: dashboard + REST API
app/orchestrator.py    fan-out: one agent per topic, semaphore-capped
app/agents/
  firecrawl_agent.py   Firecrawl /v1/search + LLM distillation
  browser_agent.py     browser-use Agent (headless Chromium)
app/llm.py             thin async client for OpenAI/Anthropic
app/db.py              SQLite job store
outputs/               digest-<job>.md files
```

## Security notes (important before sharing with the team)

- **There is no auth.** Anyone who can reach port 8000 can dispatch agents
  and spend your API credits. Either restrict the security group to your
  office/VPN IPs, use the SSH tunnel, or put nginx + basic auth in front
  before giving Evan the URL.
- Keys live only in `.env` (never committed, never sent to the browser).
- Agents scrape public web pages; respect target sites' terms of service and
  rate limits, and keep concurrency modest.

## Ideas for v2 (talking points for the Evan meeting)

- Scheduled runs (cron hitting `POST /api/jobs`) → digest emailed each morning
- Source allow-list per topic (only IAAPA, Blooloop, Amusement Today…)
- Dedup across runs so the same story doesn't reappear
- Direct export into Evan's newsletter tool via its API
- RSS-first pass (cheap) before web search (broader)
