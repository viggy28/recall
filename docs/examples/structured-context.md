<!-- recall-context: 1 -->

> Updated: `2026-11-04T23:44:31+00:00`
# synthetic-reader-investigation

Scope: Findings, qualifications, and next steps from a synthetic sample-db reader restart investigation.
Status: active

## Summary
In this synthetic test, reader A restarted while writer B did not. The test vendor now reports that a synthetic batch job triggers metadata reclamation failure ([support update](https://example.test/support-update)); this is not an independently verified root cause. No recurrence was observed after a later version upgrade; the upgrade is not a confirmed fix. The agreed next action is to reproduce the batch workload locally. Read-only savepoints need not receive subtransaction IDs, and writer-generated changes can replicate to readers; neither establishes the restart's cause. This was not a real incident.

## Current understanding
- **Observed:** On 2026-01-10, reader A restarted; writer B did not ([test logs](https://example.test/logs)).
- **Reported:** The test vendor identified a metadata reclamation failure ([support reference](https://example.test/support)) and now reports that a synthetic batch job triggers it ([support update](https://example.test/support-update)). This report is not an independently verified root cause. A later upgrade had no observed recurrence, but the upgrade remains an unconfirmed fix.
- **Technical qualifications:** Read-only savepoints need not receive subtransaction IDs. Writer-generated changes can replicate to readers. Neither statement establishes the cause of this restart.

## Open questions and next actions
- The agreed next action is to reproduce the synthetic batch workload locally.
- The investigation remains active: the vendor-reported trigger needs independent verification, and the root cause and whether the upgrade fixes it remain unresolved.

## Evidence
- [Synthetic test logs](https://example.test/logs): supplied reference for the 2026-01-10 restart observations.
- [Synthetic vendor support](https://example.test/support): supplied reference for the reported metadata reclamation failure.
- [Synthetic vendor support update](https://example.test/support-update): supplied reference for the vendor's report that a synthetic batch job triggers reclamation failure; not an independently verified root cause.
- The supplied evidence summary explicitly labels this a synthetic test session, not a real incident.
- No original session identifier, upgrade version or date, observation-window duration, support-update date, or separate references for the technical qualifications or local reproduction decision were provided.
