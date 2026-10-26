# Results

All numbers below are produced by `python scripts/evaluate.py` on the synthetic
dataset (1,981 tickets, seed 42). Raw output: [`metrics.json`](metrics.json).

## 1. Ticket classification

Trained on the oldest 1,386 tickets using agent tags (about 7% wrong on purpose),
tested on the newest 595 against the true category.

| Method | Accuracy |
|---|---|
| Majority-class baseline | 30.2% |
| Agents' own tags | 93.3% |
| **Fixproblem classifier** | **99.3%** (macro F1 0.994) |
| Fixproblem, auto-routed tickets only | 99.7% (19 sent to human review) |

Per-category F1: account_access 1.0, billing 1.0, cancellation 0.992, connectivity 0.992, delivery 1.0, technical_app 0.98.

The model beats the agent tags it was trained on because it learns the consistent pattern and
ignores the random mislabels.

## 2. Recurring-issue discovery

| Metric | Value |
|---|---|
| True issues in data | 14 |
| Clusters found | 34 |
| Homogeneity (each cluster contains one issue) | 0.908 |
| Completeness (each issue sits in one cluster) | 0.698 |
| V-measure | 0.789 |

Clusters are very pure but over-split: customers describe one problem in different words, and
TF-IDF does not know that "crashes" and "closes immediately" mean the same thing. Alerts
compensate by grouping clusters that spike together (section 3). Sentence embeddings are the
planned fix (see README, Limitations).

## 3. Emerging-issue detection (historical backtest)

| Metric | Value |
|---|---|
| False alarms in the 12 weeks before the release | 0 of 12 weekly runs |
| Days from release to first alert | 5 |
| Crash tickets received before the alert fired | 10 |
| Share of alert tickets that are truly the crash issue (precision) | 97.2% |
| Share of crash tickets captured by the alert (recall) | 100.0% |

## Caveats

The data is synthetic and generated from templates, so these numbers are an upper bound.
Real tickets are longer, messier and multilingual; expect lower accuracy and re-measure on
an anonymised export before relying on the tool.
