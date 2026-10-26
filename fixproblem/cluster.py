"""Group tickets inside each category into recurring issues.

For every predicted category we run k-means on TF-IDF vectors and choose k (2-6)
by silhouette score. Each issue is labelled with its most distinctive terms and
keeps a representative ticket (closest to the cluster centre) for context.
"""
from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field

import numpy as np
from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import silhouette_score

EXTRA_STOP = {"hi", "hello", "thanks", "team", "help", "frustrating", "account", "number", "morning",
              "good", "concern", "contacted", "previously", "callback", "come", "really", "just",
              # generic filler words that make poor issue labels
              "soon", "new", "latest", "said", "came", "got", "says", "comes", "won", "keeps", "went",
              "took", "told", "nobody", "want", "know", "day", "days", "minutes", "immediately"}


GREETING = re.compile(r"^\s*((hi|hello|hey|dear)( team| there| all)?|good (morning|afternoon|evening)|"
                      r"to whom it may concern)[\s,.!:-]*", re.I)
TAIL = re.compile(r"\s+(account number|please help|can someone|i have contacted|this is really|thanks)\b.*$", re.I)


def readable_title(text: str, limit: int = 64) -> str:
    """A short, plain-language issue title taken from the most typical ticket in the group,
    e.g. "The app crashes as soon as I log in since the update"."""
    t = GREETING.sub("", text or "").strip()
    t = TAIL.sub("", t)
    t = re.split(r"(?<=[a-z0-9])[.!?](\s|$)", t)[0].strip(" ,;:-")
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0].rstrip(",;:-") + "…"
    return (t[:1].upper() + t[1:]) if t else "Untitled issue"


@dataclass
class Issue:
    category: str
    label: str                       # readable title shown to people
    keywords: str = ""
    ticket_ids: list[str] = field(default_factory=list)
    representative: str = ""
    representative_id: str = ""


def _best_k(X, max_k: int, seed: int) -> int:
    n = X.shape[0]
    unique = len({tuple(r.indices) + tuple(np.round(r.data, 6)) for r in X})   # distinct documents
    if n < 6 or unique < 4:
        return 1                        # too little (or too repetitive) text to split meaningfully
    best_k, best_s = 1, -1.0
    for k in range(2, min(max_k, n - 1, unique - 1) + 1):
        labels = KMeans(n_clusters=k, n_init=10, random_state=seed).fit_predict(X)
        if len(set(labels)) < 2:
            continue
        s = silhouette_score(X, labels, metric="cosine", sample_size=min(n, 1500), random_state=seed)
        if s > best_s + 0.01:
            best_k, best_s = k, s
    return best_k


def discover_issues(ticket_ids: list[str], texts: list[str], categories: list[str],
                    max_k: int = 6, seed: int = 7) -> list[Issue]:
    issues: list[Issue] = []
    by_cat: dict[str, list[int]] = {}
    for i, c in enumerate(categories):
        by_cat.setdefault(c, []).append(i)
    for cat in sorted(by_cat):
        idx = by_cat[cat]
        docs = [texts[i] for i in idx]
        vec = TfidfVectorizer(stop_words=list(EXTRA_STOP | set(TfidfVectorizer(stop_words="english")
                                                              .get_stop_words())),
                              ngram_range=(1, 2), min_df=2, sublinear_tf=True)
        try:
            X = vec.fit_transform(docs)
        except ValueError:  # vocabulary empty, e.g. very few tickets
            issues.append(Issue(cat, readable_title(docs[0].split(". ", 1)[-1]), cat.replace("_", " "),
                                [ticket_ids[i] for i in idx], docs[0], ticket_ids[idx[0]]))
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")      # ConvergenceWarning on near-duplicate text is expected
            k = _best_k(X, max_k, seed)
        if k == 1:
            labels = np.zeros(len(idx), dtype=int)
            centres = np.asarray(X.mean(axis=0))
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                km = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(X)
            labels, centres = km.labels_, km.cluster_centers_
        terms = np.array(vec.get_feature_names_out())
        for c in range(k):
            members = np.where(labels == c)[0]
            if len(members) == 0:
                continue
            top = [t for t in terms[np.argsort(centres[c])[::-1]] if " " not in t][:3]
            sims = X[members] @ centres[c].T
            best = members[int(np.asarray(sims).ravel().argmax())]
            issues.append(Issue(cat, readable_title(docs[best].split(". ", 1)[-1]), ", ".join(top),
                                [ticket_ids[idx[m]] for m in members], docs[best], ticket_ids[idx[best]]))
    return issues
