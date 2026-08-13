# Q3626: lending_pool_clone_emode: cross-group or cross-bank object passes role checks [replay-of-a-previously-valid] [rollback]

## Question
Can an unprivileged attacker supply replay of a previously valid clone layout on another destination to `lending_pool_clone_emode` so `lending_pool_clone_emode` accepts a signer/object combination from the wrong group or bank, violating `emode cloning must remain authorized and source/destination-bound so no attacker can copy privileged risk settings into the wrong bank` and causing `High: unsafe live leverage configuration through unauthorized state mutation`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/emode_clone.rs` / `lending_pool_clone_emode`
- Entrypoint: `lending_pool_clone_emode`
- Attacker controls: replay of a previously valid clone layout on another destination
- Exploit idea: Probe whether role checks bind authority only to the signer and not also to the exact target object being mutated. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: emode cloning must remain authorized and source/destination-bound so no attacker can copy privileged risk settings into the wrong bank
- Expected Immunefi impact: High: unsafe live leverage configuration through unauthorized state mutation
- Fast validation: Create multiple groups/banks and assert authorized keys for one context cannot mutate another context through shared struct shape. Force the late failure branch and assert every protected field fully rolls back.
