# Q3995: lending_pool_force_tokenless_repay_complete: partial config application survives a later authorization failure [replay-of-a-previously-valid] [cross-object]

## Question
Can an unprivileged attacker make `lending_pool_force_tokenless_repay_complete` reach `lending_pool_force_tokenless_repay_complete` with replay of a previously valid layout under a new signer so some protected fields are applied before a later auth/binding failure, breaking `force-complete debt cleanup must be exact-role-bound and exact-bank-bound because it can erase live liability state` and leading to `Critical: public debt erasure or victim state corruption`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank_lite.rs` / `lending_pool_force_tokenless_repay_complete`
- Entrypoint: `lending_pool_force_tokenless_repay_complete`
- Attacker controls: replay of a previously valid layout under a new signer
- Exploit idea: Check for multi-field updates where validation may be interleaved with mutation instead of fully front-loaded. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: force-complete debt cleanup must be exact-role-bound and exact-bank-bound because it can erase live liability state
- Expected Immunefi impact: Critical: public debt erasure or victim state corruption
- Fast validation: Force the late failure branch and assert every protected field remains unchanged after rollback. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
