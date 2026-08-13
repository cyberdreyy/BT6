# Q2716: lending_pool_backfill_bank_is_t22_flag: permissionless helper accepts a forged source context [duplicate-metas-altering-which-bank] [field-scope]

## Question
Can an unprivileged attacker supply duplicate metas altering which bank is interpreted as target to `lending_pool_backfill_bank_is_t22_flag` so `lending_pool_backfill_bank_is_t22_flag` uses a forged or mismatched source context, violating `permissionless T22 backfill must touch only the intended bookkeeping field on the intended bank and never broaden live attack surface` and leading to `Medium: unauthorized state mutation or durable operational inconsistency`? Focus specifically on whether the helper writes only the exact documented fields and nothing else.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/backfill_bank_is_t22_flag.rs` / `lending_pool_backfill_bank_is_t22_flag`
- Entrypoint: `lending_pool_backfill_bank_is_t22_flag`
- Attacker controls: duplicate metas altering which bank is interpreted as target
- Exploit idea: Backfills that infer data from vote accounts, mints, seeds, or existing config must bind those sources to the bank/group deterministically. Focus specifically on whether the helper writes only the exact documented fields and nothing else.
- Invariant to test: permissionless T22 backfill must touch only the intended bookkeeping field on the intended bank and never broaden live attack surface
- Expected Immunefi impact: Medium: unauthorized state mutation or durable operational inconsistency
- Fast validation: Provide mixed candidate sources and assert the helper rejects unless the canonical source for that exact bank/group is supplied. Snapshot the full object before and after the helper and assert only the documented narrow field set can change.
