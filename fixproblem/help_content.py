"""Help-centre articles. Plain data so they can be searched and linked from contextual help."""

ARTICLES = [
    ("getting-started", "Getting started", "Getting started", """
Fixproblem analyses support data you import. It does not replace your helpdesk.
1. **Add data**: open *Imports & integrations*, download the CSV template or upload your own export.
2. **Review the import**: check accepted, rejected and duplicate counts. Download the error report for rejected rows.
3. **Explore the analysis**: the Overview shows what changed, which issues deserve attention and the evidence.
4. **Check uncertain classifications** in the Review queue.
5. **Assign an issue** to an owner and record what the team confirms.
6. **Export a report** for your manager, or invite a colleague (Team plan).
You can replay the tutorial at any time from *Help & tutorials*."""),
    ("preparing-csv", "Preparing a CSV", "Getting started", """
Required columns: **ticket_id** (unique per ticket), **created_at** (date and time), **body** (the customer's message).
Optional: subject, channel, customer_id, customer_email, customer_tier (standard, premium or business) and agent_tag (the category your team used).
Column names do not have to match: the import wizard suggests a mapping and you can correct it.
Dates: choose day/month or month/day order in the wizard. Times are stored as given in the timezone you select.
Limits: 20 MB per file. Rows with missing required values or unreadable dates are rejected with a reason; you can download them as a CSV, fix them and re-import. Re-importing the same tickets is safe: existing ticket IDs are skipped."""),
    ("priorities-alerts", "Understanding priorities and alerts", "Using Fixproblem", """
**Priority score** = tickets in the last 28 days × category severity × average customer-tier weight × (1 + log₂ of growth). The top issue scores 100.
**Emerging alerts** compare the last 14 days with the previous 8 weeks using a Poisson test, corrected for the number of issues tested, and require at least 10 tickets and at least double the expected volume.
An alert shows that demand changed. **It does not prove a root cause.** Use the supporting tickets and your team's investigation to confirm what is happening."""),
    ("reviewing", "Reviewing classifications", "Using Fixproblem", """
Each ticket gets a category and a **confidence**: how sure the model is, from 0 to 100%. Below the review threshold (55% by default) a ticket goes to the Review queue instead of being trusted automatically.
You can confirm the suggestion, choose another category, skip an ambiguous ticket or undo a correction.
**Categories:** a new workspace starts from a *starter model* trained on synthetic tickets from a fictional broadband company, so it only knows six categories (billing, connectivity, account access, delivery, cancellation, technical app). If your industry is different, many tickets will land in the review queue or in a poor category. Including your own category column (agent_tag) in the import, and correcting labels, adapts the model; configurable categories are planned.
Corrections become **eligible** for future training when the model is retrained. A single correction does not immediately change the model, and corrections are never used to score accuracy (evaluation uses separate held-out records)."""),
    ("team-access", "Managing team access", "Account", """
Roles: **Viewer/guest** can look around; **Agent** can comment and correct labels; **Analyst** can also change issue status and owner and export; **Owner/Admin** can import data and change settings.
Permissions are enforced on the server, not only hidden in the interface.
Every account has its own private workspace: other customers can never see your tickets, issues or reports. Inviting colleagues into your workspace is the next feature and is not available yet."""),
    ("billing", "Billing", "Account", """
Plans are shown on the Pricing page with their allowances. The Team plan has a 7-day trial; no card is needed to start it.
In this version payments use a clearly labelled **demo checkout** unless a Stripe test key is configured. Recurring subscriptions, webhooks and a billing portal are planned for Stage 4.
In this version Team is a one-month pass that does not renew automatically, so there is nothing to cancel."""),
    ("export-delete", "Exporting and deleting data", "Account", """
**Reports**: export the management report as CSV or PDF for the selected date range.
**Your account**: *My account → Download my data* gives your account, payments, activity history and the records stored in the workspaces you own (tickets, workflow, comments, corrections, imports, settings) as JSON. *Delete my account* removes the account and its activity history; comments remain, credited to “deleted user”.
**Retention**: activity records are kept for up to 12 months; payment records for 6 years for tax purposes.
Each workspace’s data is stored separately. Deleting your account also deletes the workspaces you own, including their database, uploads, error reports and trained model. Support requests you sent are kept for their retention period with your name and email removed."""),
]

TUTORIAL_STEPS = ["Add data", "Review the import", "Explore the first analysis", "Check uncertain classifications",
                  "Assign an issue", "Export a report or invite a colleague"]

FAQS = [
    ("What does Fixproblem do?", "It groups customer-support feedback into recurring issues, highlights changes and helps teams decide what to investigate."),
    ("Does it replace our helpdesk?", "No. It works alongside your support tools using data you import as CSV."),
    ("Can I try it without customer data?", "Yes. The demo uses clearly labelled synthetic tickets from a fictional company, and needs no account."),
    ("What happens if the classification is wrong?", "Authorised users can correct it in the Review queue. Fixproblem records who made the correction and when; corrections become eligible for training at the next retrain and can be undone."),
    ("Are results real time?", "No. Analysis runs after each import or when an admin retrains. Each page shows when the data was last analysed. Batch imports are not real time."),
    ("Can my colleagues collaborate?", "Your workspace is private. Inviting colleagues to share it is the next feature on the roadmap; until then, export reports to share results."),
    ("Can I export or delete my data?", "Yes. Reports export as CSV or PDF, and from My account you can download your personal data or delete your account. See the help article on exporting and deleting data for retention periods."),
    ("How accurate is it?", "It depends on your data. Our published evaluation uses synthetic tickets and explains its limitations; we recommend measuring on a sample of your own tickets before relying on it."),
]
