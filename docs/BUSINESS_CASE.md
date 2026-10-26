# Business case: Fixproblem

## Problem

Support teams receive thousands of messages a month, but the questions leadership
cares about, *what are customers struggling with, and what should we fix first?*, are
usually answered by someone reading tickets and building a spreadsheet once a month.
By then a new problem (a faulty app release, a billing error) has already generated
weeks of contacts, refunds and churn.

## Who benefits

| User | What they get |
|---|---|
| Head of Customer Support | A ranked list of recurring issues and early warning of new ones |
| Product and engineering leads | Evidence (ticket references) for which defects to prioritise |
| Support agents | Tickets pre-categorised; uncertain ones go to review, not the wrong queue |
| Data / insight analysts | A clean, exportable dataset instead of manual tagging |

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. Do nothing** (manual tagging + monthly spreadsheet) | No cost to build | Slow, inconsistent tags, new problems spotted late |
| **B. Helpdesk platform's built-in analytics / add-ons** | Integrated, vendor-supported | Usually per-agent licence costs; limited control over models and alert logic; data locked in the vendor |
| **C. Commercial voice-of-customer platform** | Rich features, multi-channel | Highest cost, long procurement, more than a mid-size team needs |
| **D. Lightweight in-house tool (Fixproblem)** | Low running cost, transparent and tunable rules, runs offline, data stays in-house | Needs an owner to maintain; accuracy must be validated on real data |

**Recommendation:** pilot option D for one quarter. If the pilot meets its success
criteria, continue; if the organisation later needs multi-channel analytics at scale,
the pilot's findings make a stronger, better-specified case for option B or C.

## Costs (assumptions, to be replaced with real quotes)

| Item | Assumption | Estimate |
|---|---|---|
| Build and pilot | Already built; ~10 days to connect to the real helpdesk export and validate | 10 analyst/developer days |
| Hosting | One small cloud VM or internal server, SQLite or a small Postgres instance | £20–£50 / month |
| Optional LLM summaries | Only for emerging alerts (a few calls per day) | under £10 / month |
| Maintenance | Monthly model retrain and threshold review | ~1 day / month |

## Expected benefits

These are **estimates for planning**, not measured results. Replace the inputs with the
organisation's own figures during the pilot.

Worked example for a team handling 5,000 tickets a month:

* **Manual tagging time saved.** If agents spend ~20 seconds choosing a category per ticket,
  auto-categorising the ~97% of tickets above the confidence threshold saves about
  5,000 × 0.97 × 20 s ≈ **27 hours a month**.
* **Monthly insight report.** Replacing a manual review that takes an analyst ~2 days
  a month saves **~16 hours a month**.
* **Earlier detection.** In the backtest the injected app fault was flagged
  **5 days** after release (after 10 related tickets), with no false alarms in the
  previous 12 weeks. With a monthly manual review, the same problem could go unnoticed for
  up to 4 weeks. Each week saved avoids repeat contacts, compensation and churn; the value
  depends on the organisation's cost per contact and churn rate.

## Success criteria for the pilot

1. Category accuracy of at least 85% on 300 tickets hand-checked by team leads.
2. At least one genuine emerging issue flagged earlier than the existing process, and no more
   than one false alarm a month.
3. Agents and team leads rate the dashboard useful (at least 4/5) in a short survey.

## Adoption plan

| Phase | Duration | Activities |
|---|---|---|
| 1. Shadow mode | 4 weeks | Run on a daily export; compare with agent tags; tune thresholds; no one acts on it yet |
| 2. Team-lead pilot | 6 weeks | Team leads use the dashboard in the weekly review; product team receives alerts |
| 3. Rollout | 2 weeks | Agents use the review queue; monthly report replaced; owner and retraining routine agreed |

**Risks and mitigations.** Personal data (emails) is masked for agents and the tool can run
on pseudonymised exports. Model drift is handled by monthly retraining and by tracking the
review-queue rate. If trust is low, alerts always link to the underlying tickets so people
can check the evidence themselves.
