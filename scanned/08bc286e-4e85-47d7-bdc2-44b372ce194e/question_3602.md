# Q3602: lending_pool_clone_emode: partial config application survives a later authorization failure [attacker-signer-with-valid-looking] [rollback]

## Question
Can an unprivileged attacker make `lending_pool_clone_emode` reach `lending_pool_clone_emode` with attacker signer with valid-looking source and victim destination banks so some protected fields are applied before a later auth/binding failure, breaking `emode cloning must remain authorized and source/destination-bound so no attacker can copy privileged risk settings into the wrong bank` and leading to `High: unsafe live leverage configuration through unauthorized state mutation`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/emode_clone.rs` / `lending_pool_clone_emode`
- Entrypoint: `lending_pool_clone_emode`
- Attacker controls: attacker signer with valid-looking source and victim destination banks
- Exploit idea: Check for multi-field updates where validation may be interleaved with mutation instead of fully front-loaded. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: emode cloning must remain authorized and source/destination-bound so no attacker can copy privileged risk settings into the wrong bank
- Expected Immunefi impact: High: unsafe live leverage configuration through unauthorized state mutation
- Fast validation: Force the late failure branch and assert every protected field remains unchanged after rollback. Force the late failure branch and assert every protected field fully rolls back.
