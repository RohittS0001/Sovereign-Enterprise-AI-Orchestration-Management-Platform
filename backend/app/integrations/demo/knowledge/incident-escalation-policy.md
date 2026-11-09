# Incident Escalation Policy

Owner: Engineering Operations. Applies to all production services.

## Severity levels

- **SEV1** — customer payments failing or data at risk. Page the on-call engineer and the
  engineering manager immediately; open an incident channel within 15 minutes.
- **SEV2** — degraded payments (for example elevated timeouts above 2%) or a blocked
  release-critical issue. Notify the engineering manager within 1 hour.
- **SEV3** — minor defects with a workaround. Track in Jira; no paging.

## Vendor incidents

When a third-party vendor causes a SEV1 or SEV2 incident, the engineering manager escalates
to the vendor account manager on day 1. If the vendor gives no fix date within 3 business
days, the team must switch to the documented fallback route and inform the delivery lead.

## Communication

Customer-facing updates are sent by the delivery lead, never directly by engineers. Status
updates go out every 2 hours during SEV1 and daily during SEV2.
