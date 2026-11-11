"""Central configuration. Everything is driven by environment variables (.env)."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# --- LLM provider ---------------------------------------------------------
# "none" (keyless extractive) | "claude-code" (uses your Claude Max login via the
# claude CLI, no API key) | "ollama" (free local) | "openai" | "anthropic"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "none").lower().strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-nano")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
CLAUDE_CODE_BIN = os.getenv("CLAUDE_CODE_BIN", "claude")
CLAUDE_CODE_MODEL = os.getenv("CLAUDE_CODE_MODEL", "")  # e.g. "haiku"; empty = CLI default

# --- Scraping engines ------------------------------------------------------
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")
FIRECRAWL_BASE_URL = os.getenv("FIRECRAWL_BASE_URL", "https://api.firecrawl.dev")

# --- Orchestration ---------------------------------------------------------
MAX_CONCURRENT_AGENTS = int(os.getenv("MAX_CONCURRENT_AGENTS", "4"))
ARTICLES_PER_TOPIC = int(os.getenv("ARTICLES_PER_TOPIC", "5"))
# Chars of article text sent to the LLM per summary — the main token-cost lever.
# 4000 chars ≈ 1k tokens; news articles front-load facts so little is lost.
MAX_CONTENT_CHARS = int(os.getenv("MAX_CONTENT_CHARS", "4000"))
BROWSER_AGENT_MAX_STEPS = int(os.getenv("BROWSER_AGENT_MAX_STEPS", "25"))

# --- Storage ---------------------------------------------------------------
DB_PATH = Path(os.getenv("DB_PATH", BASE_DIR / "midway.db"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "outputs"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# --- Server ----------------------------------------------------------------
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))


def llm_ready() -> bool:
    if LLM_PROVIDER == "anthropic":
        return bool(ANTHROPIC_API_KEY)
    if LLM_PROVIDER == "openai":
        return bool(OPENAI_API_KEY)
    if LLM_PROVIDER == "ollama":
        return True  # local server, no key
    if LLM_PROVIDER == "claude-code":
        import shutil
        return shutil.which(CLAUDE_CODE_BIN) is not None
    return False  # "none" -> extractive summaries


def llm_label() -> str:
    if LLM_PROVIDER == "anthropic":
        return ANTHROPIC_MODEL
    if LLM_PROVIDER == "openai":
        return OPENAI_MODEL
    if LLM_PROVIDER == "ollama":
        return f"ollama:{OLLAMA_MODEL}"
    if LLM_PROVIDER == "claude-code":
        return "claude-code (Max plan)" + (f":{CLAUDE_CODE_MODEL}" if CLAUDE_CODE_MODEL else "")
    return "none (extractive)"


def firecrawl_ready() -> bool:
    return bool(FIRECRAWL_API_KEY)


def status_summary() -> dict:
    return {
        "llm_provider": LLM_PROVIDER,
        "llm_model": llm_label(),
        "llm_configured": llm_ready(),
        "firecrawl_configured": firecrawl_ready(),
        "max_concurrent_agents": MAX_CONCURRENT_AGENTS,
    }
