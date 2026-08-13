# Q2724: lending_pool_backfill_bank_is_t22_flag: permissionless helper can be replayed to corrupt state [a-backfill-call-that-also] [field-scope]

## Question
Can an unprivileged attacker replay `lending_pool_backfill_bank_is_t22_flag` with a backfill call that also supplies a seed-backfill value so `lending_pool_backfill_bank_is_t22_flag` reapplies a helper mutation and corrupts protected state, violating `permissionless T22 backfill must touch only the intended bookkeeping field on the intended bank and never broaden live attack surface` and causing `Medium: unauthorized state mutation or durable operational inconsistency`? Focus specifically on whether the helper writes only the exact documented fields and nothing else.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/backfill_bank_is_t22_flag.rs` / `lending_pool_backfill_bank_is_t22_flag`
- Entrypoint: `lending_pool_backfill_bank_is_t22_flag`
- Attacker controls: a backfill call that also supplies a seed-backfill value
- Exploit idea: Check idempotence of public backfills and one-time transitions that should be safe no matter how many times a stranger calls them. Focus specifically on whether the helper writes only the exact documented fields and nothing else.
- Invariant to test: permissionless T22 backfill must touch only the intended bookkeeping field on the intended bank and never broaden live attack surface
- Expected Immunefi impact: Medium: unauthorized state mutation or durable operational inconsistency
- Fast validation: Run the helper repeatedly under the same state and assert the second and later invocations are true no-ops. Snapshot the full object before and after the helper and assert only the documented narrow field set can change.
