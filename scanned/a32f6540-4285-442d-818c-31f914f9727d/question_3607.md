# Q3607: lending_pool_clone_emode: partial config application survives a later authorization failure [duplicate-metas-altering-source-destination] [cross-object]

## Question
Can an unprivileged attacker make `lending_pool_clone_emode` reach `lending_pool_clone_emode` with duplicate metas altering source/destination interpretation so some protected fields are applied before a later auth/binding failure, breaking `emode cloning must remain authorized and source/destination-bound so no attacker can copy privileged risk settings into the wrong bank` and leading to `High: unsafe live leverage configuration through unauthorized state mutation`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/emode_clone.rs` / `lending_pool_clone_emode`
- Entrypoint: `lending_pool_clone_emode`
- Attacker controls: duplicate metas altering source/destination interpretation
- Exploit idea: Check for multi-field updates where validation may be interleaved with mutation instead of fully front-loaded. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: emode cloning must remain authorized and source/destination-bound so no attacker can copy privileged risk settings into the wrong bank
- Expected Immunefi impact: High: unsafe live leverage configuration through unauthorized state mutation
- Fast validation: Force the late failure branch and assert every protected field remains unchanged after rollback. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
