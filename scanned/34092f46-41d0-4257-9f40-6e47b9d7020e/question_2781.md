# Q2781: lending_pool_backfill_bank_is_t22_flag: permissionless helper bypasses paused or restricted operational state [a-bank-whose-stored-flags] [replay]

## Question
Can an unprivileged attacker call `lending_pool_backfill_bank_is_t22_flag` with a bank whose stored flags are at a boundary between multiple capabilities so `lending_pool_backfill_bank_is_t22_flag` mutates sensitive state despite paused/restricted operational state, violating `permissionless T22 backfill must touch only the intended bookkeeping field on the intended bank and never broaden live attack surface` and causing `Medium: unauthorized state mutation or durable operational inconsistency`? Focus specifically on idempotence and replay safety of public backfills and public helper mutations.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/backfill_bank_is_t22_flag.rs` / `lending_pool_backfill_bank_is_t22_flag`
- Entrypoint: `lending_pool_backfill_bank_is_t22_flag`
- Attacker controls: a bank whose stored flags are at a boundary between multiple capabilities
- Exploit idea: Public helpers still need to respect operational-state gating where mutation could affect user funds or future authorization. Focus specifically on idempotence and replay safety of public backfills and public helper mutations.
- Invariant to test: permissionless T22 backfill must touch only the intended bookkeeping field on the intended bank and never broaden live attack surface
- Expected Immunefi impact: Medium: unauthorized state mutation or durable operational inconsistency
- Fast validation: Drive the target into blocked operational states and assert the helper cannot alter sensitive fields unless explicitly allowed. Execute the helper repeatedly under unchanged state and assert later invocations are pure no-ops.
