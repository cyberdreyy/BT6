# Q3514: lending_pool_configure_bank_emode: delegate-role semantics differ across sibling config paths [a-config-call-combining-new] [rollback]

## Question
Can an unprivileged attacker use `lending_pool_configure_bank_emode` with a config call combining new emode tag and entry array edge cases so `lending_pool_configure_bank_emode` reaches a sibling configuration effect through the wrong delegate role, violating `emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions` and causing `Critical: public privilege escalation causing unsafe borrowing or liquidations`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_emode.rs` / `lending_pool_configure_bank_emode`
- Entrypoint: `lending_pool_configure_bank_emode`
- Attacker controls: a config call combining new emode tag and entry array edge cases
- Exploit idea: Compare admin/curve/limit/flow/metadata/risk/emode role assumptions across related configuration entrypoints. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions
- Expected Immunefi impact: Critical: public privilege escalation causing unsafe borrowing or liquidations
- Fast validation: Attempt each delegate signer against every related path and assert only the intended fields are mutable per role. Force the late failure branch and assert every protected field fully rolls back.
