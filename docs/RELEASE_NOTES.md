# Release notes and implementation history

The full stage-by-stage history moved here from the README.

## What is in this release

This release delivers **Stage 1** of the product specification in full, the core of **Stage 2**, and
the most important part of **Stage 3: private workspaces with tested isolation**. The rest of Stages 3–5
is listed in the [release-readiness checklist](#release-readiness-checklist).

### Private workspaces (Stage 3, isolation)
- **Every customer gets a private workspace.** Each sign-up creates its own empty workspace; nothing
  from other customers or from the demo appears in it.
- **Database-per-workspace.** Each workspace's tickets, issues, comments, labels, corrections, imports,
  search index, settings, alerts and reports live in **its own database file**
  (`instance/workspaces/ws_<id>.db`). Uploads and error reports are stored per workspace, and so are
  trained models (`instance/models/ws_<id>.pkl`). Accounts, workspaces, memberships, payments,
  activity and support requests live in a separate platform database.
- **Server-side checks on every request:** the workspace in a session is verified against the user's
  memberships, and a tampered session is corrected. Switching to a workspace you don't belong to
  returns 403.
- **Sample data never mixes with customer data.** "Explore sample data" creates a *separate* sample
  workspace (clearly labelled); a switcher in the account menu moves between workspaces.
  Loading the sample into a customer workspace is refused by the server.
- **Starter model:** a new workspace has no labelled tickets, so classification starts from a model
  trained on the synthetic sample. The workspace's own labels and review-queue corrections are added as
  they accumulate.
- **Deleting an account** removes the workspaces it owns, including their data files.
- **13 isolation tests** attack the boundary with guessed issue IDs, other workspaces' ticket IDs,
  search and the command palette, the API, exports and reports, workspace switching, tampered
  sessions, sample-data mixing and account deletion.

### Stage 1: brand, navigation and demo journey
- **Brand:** an original SVG logo (two offset message shapes with a check mark cut out of the
  negative space) as wordmark, icon, monochrome and favicon versions (`fixproblem/static/brand/`).
- **Design system:** deep navy, warm white, indigo actions; green, amber and red reserved for
  success, attention and errors. Light and dark themes, visible focus, a skip link,
  reduced-motion support and responsive layouts.
- **Public website:** hero, live product preview of clearly labelled sample data, problem, how it
  works, benefits by role, what the AI does versus what people decide, imports and integrations
  (planned connectors labelled as planned), pricing, FAQs and a final call to action. The footer
  has Product, Resources, Company and Trust groups, all working.
- **Interactive demo with no registration:** visitors open a read-only sample workspace and inspect
  issues and their evidence.
- **App navigation:** a collapsible sidebar (Overview, Tickets, Issues, Review queue, Trends, Reports,
  Imports & integrations, Team, Settings; Help & tutorials, Give feedback, Billing; workspace and
  account menu). The top bar has the page title, workspace, search, notifications and one primary action.
- **First use:** sign-up asks only for name, work email, password and workspace name. It is followed
  by a short, skippable tutorial and a 6-step checklist tracked from real actions. The tutorial is
  replayable, with a captioned video and text steps.

### Stage 2: import, investigation and reports
- **Guided CSV import:** Upload → Map columns (automatic suggestions, manual correction, preview,
  date order and timezone) → Validate (a dry run with accepted, duplicate and rejected counts and a
  downloadable error report with fixes) → Confirm → Analyse. "Data imported" and "Analysis complete"
  are shown separately. The CSV template is downloadable; there is a 20 MB limit; repeated imports
  skip existing tickets.
- **Issue investigation:**
  - an editable description
  - volume, trend, tiers and categories
  - supporting ticket excerpts
  - a priority score explained
  - model confidence
  - owner, status and activity history
  - comments
  - related issues
  - CSV export
  - a strict separation of **what the data shows**, **what Fixproblem suggests** and **what the team
    has confirmed**

  A spike is never presented as a proven root cause. Status, owner, description and confirmed notes
  survive re-analysis.
- **Review queue:** shows why each ticket needs review; you can confirm or change the label, skip it,
  or undo a correction (which restores the original prediction). Corrections are explained as
  *eligible* for future training and are kept apart from held-out evaluation data.
- **Overview:** what changed, issues that deserve attention, items awaiting review, unassigned issues
  and data freshness. Every metric has a definition, and comparisons are suppressed when the baseline
  is too small.
- **Trends:** weekly volume by category for 4, 8, 12 or 26 weeks, with a full data table as the
  accessible alternative.
- **Reports:** a management report (executive summary, scope and dates, rejected records, top issues
  with evidence, owners and status, methodology) exported as **CSV and PDF** for the selected dates.
  A **before-and-after comparison** around an intervention date includes ticket share, and is
  labelled as an observation, not proof of cause.
- **Help and support:**
  - a searchable help centre with seven articles, FAQs and the tutorial
  - contextual tooltips explaining confidence and priority
  - an in-app feedback form with category, description and an optional screenshot (type- and
    size-checked); it returns a reference number
  - diagnostics attached only with permission, never including ticket text
  - a public contact and contact-sales form
- **Honest trust pages:** Evaluation (synthetic results with limitations), Security information
  (implemented and not implemented), Service status (no fake "all systems operational"; shows only
  verifiable facts, plus a `/healthz` endpoint), Privacy, Terms, Changelog and About.
- **Pricing:** clear plan value without discount pressure: Demo, Starter, Team and Business, with
  allowances. Prices are configurable assumptions in `fixproblem/config.py`. The optional launch
  offer is **off** by default.

Everything from the earlier SupportIQ versions is kept: classification, issue discovery, emerging-issue
alerts, the issue board, ⌘K command palette, roles and masking, accounts with hashed passwords,
trials, demo checkout, data export and deletion, and owner analytics.


## 2.3: fixes from an independent code review

| Finding | Fix | Regression tests |
|---|---|---|
| A checkout reference for another account could activate Pro | Checkouts carry the account ID; activation requires Stripe to confirm paid status, the same account, amount, currency and product. Verified webhooks (HMAC signature, 5-minute tolerance) with duplicate-event and duplicate-payment protection. | 6 |
| Account deletion left the upload folder and model behind | Deleting an account removes each owned workspace's database, model file and uploads; support requests are de-identified and kept for their documented retention period. | 1 |
| "Download my data" omitted workspace records | The export includes each owned workspace's tickets, workflow, comments, label corrections, imports and settings. | 1 |
| Production could start with development defaults | Startup is refused in production with the default secret or without CSRF; demo accounts and simulated checkout are disabled; secure cookies are set. | 2 |
| Forms lacked CSRF protection | A per-session token is required on every state-changing request (form field or `X-CSRF-Token` header) and is injected into every POST form. | 3 |
| Report metrics mixed periods | Issues are ranked by tickets in the selected period; alerts appear only if the period overlaps their 14-day window; current and all-time figures are labelled. | 2 |
| The PDF table was too wide and customer text could break it | Columns are sized to the page; all customer text is escaped; long titles, markup and multi-page reports are tested. | 2 |
| Issue workflow was tied to generated labels | Issues get stable keys and IDs: each new group inherits the earlier issue holding most of its tickets, so owner, status, comments and notes follow the problem when new tickets arrive. | 2 |
| Keyword-style titles ("log / update / crashes") | Readable titles taken from the most typical ticket, with keywords kept as supporting detail. | 1 |
| Clustering warnings on tiny or duplicate data | The number of clusters is capped by the number of distinct documents. | 1 |
| Pricing promised seats and "cancel renewal" | Seats are shown as 1 user until invitations exist; Team is described as a one-month pass that does not renew. | 1 |
| CI had default permissions | The workflow runs with `contents: read`. | n/a |
