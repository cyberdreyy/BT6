# Q3640: lending_pool_clone_emode: delegate-role semantics differ across sibling config paths [duplicate-metas-altering-source-destination] [rollback]

## Question
Can an unprivileged attacker use `lending_pool_clone_emode` with duplicate metas altering source/destination interpretation so `lending_pool_clone_emode` reaches a sibling configuration effect through the wrong delegate role, violating `emode cloning must remain authorized and source/destination-bound so no attacker can copy privileged risk settings into the wrong bank` and causing `High: unsafe live leverage configuration through unauthorized state mutation`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/emode_clone.rs` / `lending_pool_clone_emode`
- Entrypoint: `lending_pool_clone_emode`
- Attacker controls: duplicate metas altering source/destination interpretation
- Exploit idea: Compare admin/curve/limit/flow/metadata/risk/emode role assumptions across related configuration entrypoints. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: emode cloning must remain authorized and source/destination-bound so no attacker can copy privileged risk settings into the wrong bank
- Expected Immunefi impact: High: unsafe live leverage configuration through unauthorized state mutation
- Fast validation: Attempt each delegate signer against every related path and assert only the intended fields are mutable per role. Force the late failure branch and assert every protected field fully rolls back.
