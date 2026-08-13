# Q3507: lending_pool_configure_bank_emode: delegate-role semantics differ across sibling config paths [two-banks-where-emode-entries] [cross-object]

## Question
Can an unprivileged attacker use `lending_pool_configure_bank_emode` with two banks where emode entries can be cross-applied by shape only so `lending_pool_configure_bank_emode` reaches a sibling configuration effect through the wrong delegate role, violating `emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions` and causing `Critical: public privilege escalation causing unsafe borrowing or liquidations`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_emode.rs` / `lending_pool_configure_bank_emode`
- Entrypoint: `lending_pool_configure_bank_emode`
- Attacker controls: two banks where emode entries can be cross-applied by shape only
- Exploit idea: Compare admin/curve/limit/flow/metadata/risk/emode role assumptions across related configuration entrypoints. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions
- Expected Immunefi impact: Critical: public privilege escalation causing unsafe borrowing or liquidations
- Fast validation: Attempt each delegate signer against every related path and assert only the intended fields are mutable per role. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
