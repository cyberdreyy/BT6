# Q2701: lending_pool_backfill_bank_is_t22_flag: permissionless helper rewrites the wrong protected fields [a-bank-whose-stored-flags] [replay]

## Question
Can an unprivileged attacker invoke `lending_pool_backfill_bank_is_t22_flag` with a bank whose stored flags are at a boundary between multiple capabilities so `lending_pool_backfill_bank_is_t22_flag` rewrites more protected state than intended for a permissionless helper, breaking `permissionless T22 backfill must touch only the intended bookkeeping field on the intended bank and never broaden live attack surface` and causing `Medium: unauthorized state mutation or durable operational inconsistency`? Focus specifically on idempotence and replay safety of public backfills and public helper mutations.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/backfill_bank_is_t22_flag.rs` / `lending_pool_backfill_bank_is_t22_flag`
- Entrypoint: `lending_pool_backfill_bank_is_t22_flag`
- Attacker controls: a bank whose stored flags are at a boundary between multiple capabilities
- Exploit idea: Public backfills and helpers must touch only narrow, deterministic fields; probe for broader mutation than design intends. Focus specifically on idempotence and replay safety of public backfills and public helper mutations.
- Invariant to test: permissionless T22 backfill must touch only the intended bookkeeping field on the intended bank and never broaden live attack surface
- Expected Immunefi impact: Medium: unauthorized state mutation or durable operational inconsistency
- Fast validation: Snapshot the full object before/after the helper and assert only the exact documented fields can change. Execute the helper repeatedly under unchanged state and assert later invocations are pure no-ops.
