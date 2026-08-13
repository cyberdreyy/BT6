# Q3588: lending_pool_clone_emode: public caller bypasses role-bound configuration [two-banks-from-different-groups] [rollback]

## Question
Can an unprivileged attacker invoke `lending_pool_clone_emode` with two banks from different groups sharing compatible layouts so `lending_pool_clone_emode` applies a group/bank configuration change without the intended role, violating `emode cloning must remain authorized and source/destination-bound so no attacker can copy privileged risk settings into the wrong bank` and causing `High: unsafe live leverage configuration through unauthorized state mutation`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/emode_clone.rs` / `lending_pool_clone_emode`
- Entrypoint: `lending_pool_clone_emode`
- Attacker controls: two banks from different groups sharing compatible layouts
- Exploit idea: Attack every signer, delegate-role, and group/bank binding assumption on the path so configuration writes cannot be reached by a normal user. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: emode cloning must remain authorized and source/destination-bound so no attacker can copy privileged risk settings into the wrong bank
- Expected Immunefi impact: High: unsafe live leverage configuration through unauthorized state mutation
- Fast validation: Use attacker-controlled signer/accounts against the config path and assert no protected field changes unless the exact authorized role signs. Force the late failure branch and assert every protected field fully rolls back.
