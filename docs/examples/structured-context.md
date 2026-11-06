<!-- recall-context: 2 -->
# Synthetic reader restart investigation

## Summary
In this fictional example, reader A restarted while writer B did not. The test vendor attributes a metadata reclamation failure to a synthetic batch job, but that report has not been independently verified. No recurrence after a later upgrade does not establish a fix.

## Open questions and next actions
- Reproduce the synthetic batch workload locally (agreed in the test evidence).

---

Supporting detail follows.

## Current understanding
- The synthetic logs date the restart to January 10, 2026. [E1]
- The vendor's later response names a possible workload trigger beyond its earlier reclamation-failure diagnosis. The supplied record does not include independent diagnostic evidence. [E2][E3]

## Details
Read-only savepoints need not receive subtransaction IDs. Writer-generated changes can replicate to readers; the location of a restart therefore does not establish where the workload originated. Separate references for these technical qualifications were not supplied in this fictional example. [E4]

## Evidence
- **E1** — [Synthetic test logs](https://example.test/logs): supplied reference for the restart observations.
- **E2** — [Synthetic vendor support](https://example.test/support): supplied reference for the reported reclamation failure.
- **E3** — [Synthetic vendor update](https://example.test/support-update): supplied reference for the batch-workload attribution, not an independently verified root cause.
- **E4** — Fictional test evidence summary: source of the technical qualifications and agreed local reproduction action; no separate link supplied. No original session identifier, upgrade version or date, observation-window duration, or support-update date was provided.

### Context metadata
- Scope: Findings, qualifications, and next steps in a fictional reader restart investigation.
- Status: active
- Evidence through: Not recorded in the fictional test evidence.
