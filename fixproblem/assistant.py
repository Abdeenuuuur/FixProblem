"""Fixproblem assistant: answers questions and helps users do tasks.

Design (safe by default)
------------------------
1. **Grounded answers.** Questions about the product are answered from the help centre, FAQs and task
   guides (retrieved with TF-IDF). Every answer links its sources.
2. **Workspace questions** ("what should we fix first?", "what changed?") are answered by read-only
   queries on the *current workspace only*, using the same connection the page uses.
3. **Actions are suggestions, never automatic.** The assistant returns buttons (assign to me, open the
   import wizard, download a report...). Each button calls a normal route, so the usual permission and
   plan checks apply, and the user decides whether to click.
4. **Optional LLM** (Anthropic Claude, OpenAI or Google Gemini) only rewrites the answer in natural
   language from the retrieved documentation and aggregate facts. It cannot create actions. Ticket text
   is NOT sent unless an admin enables `share_ticket_text`. Any provider failure falls back to the
   built-in answer, so the assistant always works.
5. **Human escalation** is offered on every answer; there is no staffed live chat and none is claimed.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from dataclasses import dataclass, field

from .help_content import ARTICLES, FAQS

log = logging.getLogger(__name__)

TASK_GUIDES = [
    ("task-import", "How do I upload my tickets?", "/help/preparing-csv",
     "Open Imports & integrations and click Next: map columns after choosing your CSV. Fixproblem suggests a match "
     "for each column, shows which rows will be imported and why any are rejected, then imports and analyses after "
     "you confirm. Download the CSV template if you are starting from scratch."),
    ("task-review", "How do I check uncertain tickets?", "/help/reviewing",
     "Open the Review queue. For each ticket choose the right category, skip it if it is ambiguous, or undo a "
     "correction. Corrections are recorded and become eligible for training at the next retrain."),
    ("task-assign", "How do I assign an issue?", "/help/getting-started",
     "Open an issue and choose an owner and status in the panel on the right. Analysts, owners and admins can do "
     "this; it is recorded in the issue's activity history."),
    ("task-report", "How do I share results with my manager?", "/help/export-delete",
     "Open Reports, choose the dates and download the PDF or CSV. You can also compare ticket volume before and "
     "after a change you made."),
    ("task-sample", "Can I try it with sample data?", "/demo",
     "Yes. On the website click Try the interactive demo. If you have an account, use Explore sample data in the "
     "account menu: it opens a separate sample workspace that never mixes with your own data."),
    ("task-invite", "Can I invite colleagues?", "/help/team-access",
     "Not yet. Each workspace is private to its owner. Invitations are the next feature on the roadmap; until then "
     "share results by exporting a report."),
    ("task-delete", "How do I delete my data?", "/help/export-delete",
     "Go to My account. Download my data gives you everything held about you; Delete my account removes the account "
     "and the workspaces you own, including their data."),
    ("task-price", "How much does it cost?", "/pricing",
     "Starter is free during the pilot and includes importing your own tickets. Team is a £9.59 one-month pass (a provisional "
     "price, no automatic renewal) with a 7-day trial, and adds alerts, report exports and AI settings. Business is by agreement."),
]


@dataclass
class Reply:
    text: str
    sources: list = field(default_factory=list)       # [{"title", "url"}]
    actions: list = field(default_factory=list)       # [{"label", "url", "method"}]
    suggestions: list = field(default_factory=list)
    provider: str = "built-in"

    def as_dict(self) -> dict:
        return {"text": self.text, "sources": self.sources, "actions": self.actions,
                "suggestions": self.suggestions, "provider": self.provider,
                "escalate": {"label": "Talk to a person", "url": "/feedback"}}


# ---------------------------------------------------------------- documentation retrieval
class Knowledge:
    def __init__(self):
        from sklearn.feature_extraction.text import TfidfVectorizer
        self.docs = []
        for slug, title, _section, text in ARTICLES:
            for para in [p.strip() for p in re.split(r"\n(?=\d\.|\*\*|[A-Z])", text.strip()) if p.strip()]:
                self.docs.append({"title": title, "url": f"/help/{slug}", "text": re.sub(r"\*+", "", para)})
        for q, a in FAQS:
            self.docs.append({"title": q, "url": "/#faq", "text": f"{q} {a}", "answer": a})
        for _id, q, url, a in TASK_GUIDES:
            self.docs.append({"title": q, "url": url, "text": f"{q} {a}", "answer": a})
        self.vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
        self.matrix = self.vec.fit_transform([d["text"] for d in self.docs])

    def search(self, question: str, k: int = 3) -> list[tuple[float, dict]]:
        q = self.vec.transform([question])
        scores = (self.matrix @ q.T).toarray().ravel()
        order = scores.argsort()[::-1][:k]
        return [(float(scores[i]), self.docs[i]) for i in order if scores[i] > 0]


_KB: Knowledge | None = None


def kb() -> Knowledge:
    global _KB
    if _KB is None:
        _KB = Knowledge()
    return _KB


# ---------------------------------------------------------------- workspace tools (read-only)
def _has(q: str, *words) -> bool:
    return any(re.search(rf"\b{w}", q) for w in words)


def workspace_answer(q: str, conn, can, user: str | None) -> Reply | None:
    """Answer questions about the current workspace's data. Returns None if not a data question."""
    q = q.lower()
    n = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    if n == 0 and _has(q, "issue", "fix", "ticket", "changed", "spike", "review", "report", "priorit"):
        return Reply("This workspace has no tickets yet, so there is nothing to analyse. Upload a CSV, or explore "
                     "the separate sample workspace first.",
                     actions=[{"label": "Upload my tickets", "url": "/import", "method": "get"}],
                     sources=[{"title": "Preparing a CSV", "url": "/help/preparing-csv"}])

    m = re.search(r"(?:find|search|show|tickets?)\s+(?:for|about|mentioning|on)\s+(.+)", q)
    if m:
        term = re.sub(r"[^\w\s-]", "", m.group(1)).strip()[:60]
        words = [w for w in term.split() if w]
        if words:
            fts = " ".join(f'"{w}"*' for w in words[:6])
            count = conn.execute("SELECT COUNT(*) FROM tickets_fts WHERE tickets_fts MATCH ?", (fts,)).fetchone()[0]
            return Reply(f"I found {count} ticket{'s' if count != 1 else ''} mentioning “{term}”.",
                         actions=[{"label": f"Show tickets about “{term}”", "url": f"/inbox?q={term}", "method": "get"}])

    if _has(q, "what changed", "changed", "emerg", "spike", "alert", "new problem", "trend"):
        alerts = conn.execute("SELECT * FROM alerts ORDER BY growth DESC").fetchall()
        if not alerts:
            return Reply("Nothing is unusual right now: every category is within its normal range compared with the "
                         "previous 8 weeks.", actions=[{"label": "Open Trends", "url": "/trends", "method": "get"}])
        a = alerts[0]
        return Reply(f"{len(alerts)} emerging issue{'s' if len(alerts) > 1 else ''}. The biggest: "
                     f"{a['category'].replace('_', ' ')} received {a['recent']} tickets in the last 14 days against "
                     f"about {a['expected']:.1f} expected. This shows that demand changed, not why; check the tickets.",
                     actions=[{"label": f"Inspect the {a['tickets']} tickets", "url": f"/alerts/{a['alert_id']}", "method": "get"}],
                     sources=[{"title": "Understanding priorities and alerts", "url": "/help/priorities-alerts"}])

    if _has(q, "unassigned", "no owner", "nobody"):
        rows = conn.execute("SELECT i.* FROM issues i LEFT JOIN issue_meta m ON m.key=i.key "
                            "WHERE m.owner IS NULL ORDER BY i.priority DESC LIMIT 3").fetchall()
        total = conn.execute("SELECT COUNT(*) FROM issues i LEFT JOIN issue_meta m ON m.key=i.key WHERE m.owner IS NULL").fetchone()[0]
        acts = [{"label": f"Open “{r['label']}”", "url": f"/issues/{r['issue_id']}", "method": "get"} for r in rows]
        if rows and can("triage") and user:
            acts.insert(0, {"label": f"Assign “{rows[0]['label']}” to me", "url": f"/issues/{rows[0]['issue_id']}/update",
                            "method": "post", "fields": {"owner": user}})
        return Reply(f"{total} issue{'s are' if total != 1 else ' is'} unassigned. The highest priority: "
                     + ", ".join(r["label"] for r in rows) + ".", actions=acts)

    if _has(q, "fix first", "priorit", "top issue", "biggest", "most important", "what should", "worst"):
        rows = conn.execute("SELECT * FROM issues ORDER BY priority DESC LIMIT 3").fetchall()
        lines = [f"{k + 1}. {r['label']}: {r['size']} tickets, {r['recent']} in the last 14 days"
                 + (" (emerging)" if r["emerging"] else "") for k, r in enumerate(rows)]
        acts = [{"label": f"Open “{rows[0]['label']}”", "url": f"/issues/{rows[0]['issue_id']}", "method": "get"}]
        if can("triage") and user:
            acts.append({"label": "Assign it to me", "url": f"/issues/{rows[0]['issue_id']}/update", "method": "post",
                         "fields": {"owner": user}})
            acts.append({"label": "Mark as investigating", "url": f"/issues/{rows[0]['issue_id']}/update", "method": "post",
                         "fields": {"status": "investigating"}})
        return Reply("Ranked by volume, severity, customer tier and growth, the top issues are:\n" + "\n".join(lines),
                     actions=acts, sources=[{"title": "Understanding priorities and alerts", "url": "/help/priorities-alerts"}])

    if _has(q, "review", "uncertain", "unsure", "confidence"):
        k = conn.execute("SELECT COUNT(*) FROM tickets WHERE needs_review=1").fetchone()[0]
        return Reply(f"{k} ticket{'s' if k != 1 else ''} need{'s' if k == 1 else ''} a person to check the category, "
                     "because the model was less than 55% sure.",
                     actions=[{"label": "Open the review queue", "url": "/review", "method": "get"}],
                     sources=[{"title": "Reviewing classifications", "url": "/help/reviewing"}])

    if _has(q, "report", "export", "pdf", "manager", "share"):
        if can("export"):
            return Reply("Your management report is ready for the full date range of this workspace.",
                         actions=[{"label": "Download PDF report", "url": "/reports/report.pdf", "method": "get"},
                                  {"label": "Download CSV", "url": "/reports/export.csv", "method": "get"},
                                  {"label": "Open Reports", "url": "/reports", "method": "get"}])
        return Reply("You can read the management report on the Reports page. Downloading it as PDF or CSV needs the "
                     "Team plan.", actions=[{"label": "Open Reports", "url": "/reports", "method": "get"},
                                            {"label": "See plans", "url": "/billing", "method": "get"}])

    if _has(q, "how many tickets", "ticket count", "volume"):
        return Reply(f"This workspace has {n:,} tickets.", actions=[{"label": "Open Tickets", "url": "/inbox", "method": "get"}])
    return None


# ---------------------------------------------------------------- optional LLM providers
SYSTEM = ("You are the help assistant inside Fixproblem, a tool that analyses customer-support tickets. "
          "Answer ONLY from the documentation and workspace facts provided. If they do not contain the answer, say "
          "you are not sure and suggest contacting a person. Never invent features, prices, integrations or "
          "guarantees. Never claim a spike proves a root cause. Be brief: at most 4 sentences, plain English.")


def provider_config() -> tuple[str | None, str | None, str | None]:
    """(provider, api_key, model) from environment variables. Keys are never stored in the database."""
    prov = (os.getenv("FIXPROBLEM_AI_PROVIDER") or "").lower() or None
    keys = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}
    if prov is None:
        prov = next((p for p, k in keys.items() if os.getenv(k)), None)
    if prov not in keys:
        return None, None, None
    defaults = {"anthropic": "claude-sonnet-4-6", "openai": "gpt-4o-mini", "gemini": "gemini-1.5-flash"}
    return prov, os.getenv(keys[prov]), os.getenv("FIXPROBLEM_AI_MODEL") or defaults[prov]


def _post(url: str, payload: dict, headers: dict, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"content-type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def call_llm(prompt: str, provider: str, key: str, model: str) -> str:
    if provider == "anthropic":
        d = _post("https://api.anthropic.com/v1/messages",
                  {"model": model, "max_tokens": 400, "system": SYSTEM, "messages": [{"role": "user", "content": prompt}]},
                  {"x-api-key": key, "anthropic-version": "2023-06-01"})
        return "".join(b.get("text", "") for b in d.get("content", []))
    if provider == "openai":
        d = _post("https://api.openai.com/v1/chat/completions",
                  {"model": model, "max_tokens": 400, "messages": [{"role": "system", "content": SYSTEM},
                                                                   {"role": "user", "content": prompt}]},
                  {"authorization": f"Bearer {key}"})
        return d["choices"][0]["message"]["content"]
    if provider == "gemini":
        d = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
                  {"systemInstruction": {"parts": [{"text": SYSTEM}]},
                   "contents": [{"role": "user", "parts": [{"text": prompt}]}]}, {})
        return "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
    raise ValueError("unknown provider")


def redact(text: str, conn) -> str:
    """Remove anything derived from customers' words before text leaves the app: issue names are built
    from ticket wording, and quoted phrases may be search terms or excerpts. Counts and categories stay."""
    if conn is not None:
        labels = sorted((r[0] for r in conn.execute("SELECT label FROM issues")), key=len, reverse=True)
        for label in labels:
            text = text.replace(label, "[issue name withheld]")
    return re.sub(r"“[^”]*”", "“[withheld]”", text)


# ---------------------------------------------------------------- main entry point
def answer(question: str, conn=None, can=lambda p: False, user: str | None = None,
           share_ticket_text: bool = False, llm=None) -> Reply:
    question = (question or "").strip()[:500]
    if not question:
        return Reply("Ask me anything about Fixproblem, or about this workspace’s data.", suggestions=SUGGESTIONS)

    reply = workspace_answer(question, conn, can, user) if conn is not None else None
    hits = kb().search(question)
    if reply is None:
        if hits and hits[0][0] >= 0.12:
            best = hits[0][1]
            text = best.get("answer") or best["text"]
            reply = Reply(text, sources=[{"title": h["title"], "url": h["url"]} for _, h in hits[:2]])
        else:
            reply = Reply("I’m not sure about that. I can answer questions about using Fixproblem and about this "
                          "workspace’s data. For anything else, a person can help.",
                          suggestions=SUGGESTIONS)

    # Optional: let an LLM phrase the answer, grounded in the retrieved docs and the facts we computed.
    prov, key, model = provider_config()
    if llm is not None or (prov and key):
        docs = "\n".join(f"- [{h['title']}] {h['text']}" for _, h in hits[:3])
        facts = reply.text if share_ticket_text else redact(reply.text, conn)
        prompt = f"Question: {question}\n\nDocumentation:\n{docs or '(none)'}\n\nFacts from the app:\n{facts}"
        try:
            text = (llm(prompt) if llm else call_llm(prompt, prov, key, model)).strip()
            if text:
                reply.text, reply.provider = text, (prov or "llm")
        except Exception as exc:              # network, quota, auth, bad response: keep the built-in answer
            log.warning("assistant LLM failed, using built-in answer: %s", exc)
    if not reply.suggestions:
        reply.suggestions = SUGGESTIONS[:3]
    return reply


SUGGESTIONS = ["What should we fix first?", "What changed recently?", "How do I upload my tickets?",
               "Which issues are unassigned?", "How much does it cost?", "Can I try it with sample data?"]
