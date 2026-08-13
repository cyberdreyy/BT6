# Q4000: lending_pool_force_tokenless_repay_complete: partial config application survives a later authorization failure [a-user-whose-account-still] [rollback]

## Question
Can an unprivileged attacker make `lending_pool_force_tokenless_repay_complete` reach `lending_pool_force_tokenless_repay_complete` with a user whose account still references the target bank in active balances so some protected fields are applied before a later auth/binding failure, breaking `force-complete debt cleanup must be exact-role-bound and exact-bank-bound because it can erase live liability state` and leading to `Critical: public debt erasure or victim state corruption`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank_lite.rs` / `lending_pool_force_tokenless_repay_complete`
- Entrypoint: `lending_pool_force_tokenless_repay_complete`
- Attacker controls: a user whose account still references the target bank in active balances
- Exploit idea: Check for multi-field updates where validation may be interleaved with mutation instead of fully front-loaded. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: force-complete debt cleanup must be exact-role-bound and exact-bank-bound because it can erase live liability state
- Expected Immunefi impact: Critical: public debt erasure or victim state corruption
- Fast validation: Force the late failure branch and assert every protected field remains unchanged after rollback. Force the late failure branch and assert every protected field fully rolls back.
