# Q2794: lending_pool_backfill_bank_is_t22_flag: public helper binds the right object but the wrong authority derivative [candidate-banks-from-another-group] [field-scope]

## Question
Can an unprivileged attacker use `lending_pool_backfill_bank_is_t22_flag` with candidate banks from another group with similar structure so `lending_pool_backfill_bank_is_t22_flag` updates the correct object but derives or stores the wrong authority-like value, violating `permissionless T22 backfill must touch only the intended bookkeeping field on the intended bank and never broaden live attack surface` and leading to `Medium: unauthorized state mutation or durable operational inconsistency`? Focus specifically on whether the helper writes only the exact documented fields and nothing else.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/backfill_bank_is_t22_flag.rs` / `lending_pool_backfill_bank_is_t22_flag`
- Entrypoint: `lending_pool_backfill_bank_is_t22_flag`
- Attacker controls: candidate banks from another group with similar structure
- Exploit idea: Probe backfills and validators that write vote-account bindings, seed-derived addresses, or authority metadata used later by privileged flows. Focus specifically on whether the helper writes only the exact documented fields and nothing else.
- Invariant to test: permissionless T22 backfill must touch only the intended bookkeeping field on the intended bank and never broaden live attack surface
- Expected Immunefi impact: Medium: unauthorized state mutation or durable operational inconsistency
- Fast validation: Compare stored derived authorities against canonical derivation for the same object across replay and mismatch attempts. Snapshot the full object before and after the helper and assert only the documented narrow field set can change.
