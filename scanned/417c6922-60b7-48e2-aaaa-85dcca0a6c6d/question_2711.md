# Q2711: lending_pool_backfill_bank_is_t22_flag: permissionless helper accepts a forged source context [same-slot-backfill-followed-by] [replay]

## Question
Can an unprivileged attacker supply same-slot backfill followed by a value-moving bank action to `lending_pool_backfill_bank_is_t22_flag` so `lending_pool_backfill_bank_is_t22_flag` uses a forged or mismatched source context, violating `permissionless T22 backfill must touch only the intended bookkeeping field on the intended bank and never broaden live attack surface` and leading to `Medium: unauthorized state mutation or durable operational inconsistency`? Focus specifically on idempotence and replay safety of public backfills and public helper mutations.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/backfill_bank_is_t22_flag.rs` / `lending_pool_backfill_bank_is_t22_flag`
- Entrypoint: `lending_pool_backfill_bank_is_t22_flag`
- Attacker controls: same-slot backfill followed by a value-moving bank action
- Exploit idea: Backfills that infer data from vote accounts, mints, seeds, or existing config must bind those sources to the bank/group deterministically. Focus specifically on idempotence and replay safety of public backfills and public helper mutations.
- Invariant to test: permissionless T22 backfill must touch only the intended bookkeeping field on the intended bank and never broaden live attack surface
- Expected Immunefi impact: Medium: unauthorized state mutation or durable operational inconsistency
- Fast validation: Provide mixed candidate sources and assert the helper rejects unless the canonical source for that exact bank/group is supplied. Execute the helper repeatedly under unchanged state and assert later invocations are pure no-ops.
