"""Issue summaries.

By default summaries are extractive and deterministic (no external calls). If
`FIXPROBLEM_LLM_API_KEY` is set, an LLM writes a one-sentence summary instead.
Any LLM failure falls back to the extractive summary, so the platform never
depends on an external service being available.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request

log = logging.getLogger(__name__)


def extractive_summary(label: str, count: int, representative: str) -> str:
    example = representative if len(representative) <= 140 else representative[:137] + "..."
    return f"{count} tickets mentioning {label.replace(' / ', ', ')}. Typical example: \u201c{example}\u201d"


class LLMSummariser:
    """Calls an Anthropic-compatible Messages API. Injected so tests can fake it."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6",
                 url: str = "https://api.anthropic.com/v1/messages", timeout: int = 20):
        self.api_key, self.model, self.url, self.timeout = api_key, model, url, timeout

    def __call__(self, examples: list[str]) -> str:
        prompt = ("Summarise the common customer problem in these support tickets in one plain sentence, "
                  "no preamble:\n- " + "\n- ".join(examples[:15]))
        req = urllib.request.Request(
            self.url, method="POST",
            data=json.dumps({"model": self.model, "max_tokens": 120,
                             "messages": [{"role": "user", "content": prompt}]}).encode(),
            headers={"content-type": "application/json", "x-api-key": self.api_key,
                     "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
        return "".join(b.get("text", "") for b in data.get("content", [])).strip()


def get_llm():
    key = os.getenv("FIXPROBLEM_LLM_API_KEY")
    return LLMSummariser(key) if key else None


def summarise(label: str, examples: list[str], representative: str, llm=None) -> str:
    if llm is not None:
        try:
            text = llm(examples)
            if text:
                return text
        except Exception as exc:  # network, auth, rate limit, bad JSON...
            log.warning("LLM summary failed, using extractive summary: %s", exc)
    return extractive_summary(label, len(examples), representative)
