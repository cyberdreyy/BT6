# Q3710: lending_pool_clone_emode: clone or copy helper can duplicate privileged state into the wrong object [candidate-destinations-from-another-bank] [rollback]

## Question
Can an unprivileged attacker make `lending_pool_clone_emode` reach `lending_pool_clone_emode` with candidate destinations from another bank family so protected state is cloned or copied into the wrong destination, violating `emode cloning must remain authorized and source/destination-bound so no attacker can copy privileged risk settings into the wrong bank` and causing `High: unsafe live leverage configuration through unauthorized state mutation`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/emode_clone.rs` / `lending_pool_clone_emode`
- Entrypoint: `lending_pool_clone_emode`
- Attacker controls: candidate destinations from another bank family
- Exploit idea: Attack any helper that duplicates config, fee state, emode, or metadata across objects and must bind source and destination tightly. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: emode cloning must remain authorized and source/destination-bound so no attacker can copy privileged risk settings into the wrong bank
- Expected Immunefi impact: High: unsafe live leverage configuration through unauthorized state mutation
- Fast validation: Use mixed-validity source/destination objects and assert no protected state lands on an attacker-selected destination. Force the late failure branch and assert every protected field fully rolls back.
