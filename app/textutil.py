"""Keyword relevance scoring + extractive summaries.

This is what makes zero-key mode work: when no LLM is configured, we rank
candidate articles by topic-keyword overlap and summarize by taking the lead
sentences of the extracted article text (news writing front-loads the facts,
so lead-based extraction is a decent stand-in for abstractive summaries)."""
import re

STOPWORDS = set(
    "a an and are as at be by for from has have in is it its of on or that the "
    "this to was were will with about into over under new news recent latest "
    "industry update updates".split()
)


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower())
            if w not in STOPWORDS and len(w) > 2]


def keyword_score(topic: str, text: str) -> int:
    """How many distinct topic keywords appear in the text."""
    topic_tokens = set(tokens(topic))
    if not topic_tokens:
        return 0
    hay = text.lower()
    return sum(1 for t in topic_tokens if t in hay)


def extractive_summary(text: str, max_sentences: int = 3, max_chars: int = 600) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(sentences[:max_sentences])[:max_chars].strip()
