"""Ticket category classifier.

A TF-IDF + logistic regression model trained on the categories agents assigned
in the past (`agent_tag`). It is fast, explainable and runs offline. Tickets
whose top-class probability is below `review_threshold` are flagged for a human
to check, rather than trusted blindly.
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.feature_extraction.text import TfidfVectorizer


def build_model() -> Pipeline:
    features = FeatureUnion([
        ("words", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True, stop_words="english")),
        ("chars", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=3, sublinear_tf=True)),
    ])
    clf = LogisticRegression(max_iter=2000, C=4.0, class_weight="balanced")
    return Pipeline([("features", features), ("clf", clf)])


def ticket_text(subject: str | None, body: str | None) -> str:
    return f"{subject or ''}. {body or ''}".strip()


@dataclass
class Prediction:
    category: str
    confidence: float
    needs_review: bool


class TicketClassifier:
    def __init__(self, review_threshold: float = 0.55):
        self.review_threshold = review_threshold
        self.model: Pipeline | None = None

    def fit(self, texts: list[str], labels: list[str]) -> "TicketClassifier":
        if len(set(labels)) < 2:
            raise ValueError("need at least two categories to train")
        self.model = build_model().fit(texts, labels)
        return self

    @property
    def classes(self) -> list[str]:
        self._require()
        return list(self.model.classes_)

    def predict(self, texts: list[str]) -> list[Prediction]:
        self._require()
        if not texts:
            return []
        proba = self.model.predict_proba(texts)
        idx = proba.argmax(axis=1)
        conf = proba[np.arange(len(texts)), idx]
        return [Prediction(self.model.classes_[i], float(c), bool(c < self.review_threshold))
                for i, c in zip(idx, conf)]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            pickle.dump(self.model, f)

    @classmethod
    def load(cls, path: Path, review_threshold: float = 0.55) -> "TicketClassifier":
        inst = cls(review_threshold)
        with path.open("rb") as f:
            inst.model = pickle.load(f)
        return inst

    def _require(self) -> None:
        if self.model is None:
            raise RuntimeError("classifier has not been trained")
