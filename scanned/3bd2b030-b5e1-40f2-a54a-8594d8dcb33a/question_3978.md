# Q3978: lending_pool_force_tokenless_repay_complete: public caller bypasses role-bound configuration [a-bank-with-dust-sized] [rollback]

## Question
Can an unprivileged attacker invoke `lending_pool_force_tokenless_repay_complete` with a bank with dust-sized but live liabilities at the boundary so `lending_pool_force_tokenless_repay_complete` applies a group/bank configuration change without the intended role, violating `force-complete debt cleanup must be exact-role-bound and exact-bank-bound because it can erase live liability state` and causing `Critical: public debt erasure or victim state corruption`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank_lite.rs` / `lending_pool_force_tokenless_repay_complete`
- Entrypoint: `lending_pool_force_tokenless_repay_complete`
- Attacker controls: a bank with dust-sized but live liabilities at the boundary
- Exploit idea: Attack every signer, delegate-role, and group/bank binding assumption on the path so configuration writes cannot be reached by a normal user. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: force-complete debt cleanup must be exact-role-bound and exact-bank-bound because it can erase live liability state
- Expected Immunefi impact: Critical: public debt erasure or victim state corruption
- Fast validation: Use attacker-controlled signer/accounts against the config path and assert no protected field changes unless the exact authorized role signs. Force the late failure branch and assert every protected field fully rolls back.
