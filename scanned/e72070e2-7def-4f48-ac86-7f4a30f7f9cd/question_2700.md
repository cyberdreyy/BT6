# Q2700: lending_pool_backfill_bank_is_t22_flag: permissionless helper rewrites the wrong protected fields [duplicate-metas-altering-which-bank] [field-scope]

## Question
Can an unprivileged attacker invoke `lending_pool_backfill_bank_is_t22_flag` with duplicate metas altering which bank is interpreted as target so `lending_pool_backfill_bank_is_t22_flag` rewrites more protected state than intended for a permissionless helper, breaking `permissionless T22 backfill must touch only the intended bookkeeping field on the intended bank and never broaden live attack surface` and causing `Medium: unauthorized state mutation or durable operational inconsistency`? Focus specifically on whether the helper writes only the exact documented fields and nothing else.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/backfill_bank_is_t22_flag.rs` / `lending_pool_backfill_bank_is_t22_flag`
- Entrypoint: `lending_pool_backfill_bank_is_t22_flag`
- Attacker controls: duplicate metas altering which bank is interpreted as target
- Exploit idea: Public backfills and helpers must touch only narrow, deterministic fields; probe for broader mutation than design intends. Focus specifically on whether the helper writes only the exact documented fields and nothing else.
- Invariant to test: permissionless T22 backfill must touch only the intended bookkeeping field on the intended bank and never broaden live attack surface
- Expected Immunefi impact: Medium: unauthorized state mutation or durable operational inconsistency
- Fast validation: Snapshot the full object before/after the helper and assert only the exact documented fields can change. Snapshot the full object before and after the helper and assert only the documented narrow field set can change.
