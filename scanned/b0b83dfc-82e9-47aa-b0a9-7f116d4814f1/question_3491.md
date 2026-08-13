# Q3491: lending_pool_configure_bank_emode: cross-group or cross-bank object passes role checks [two-banks-where-emode-entries] [cross-object]

## Question
Can an unprivileged attacker supply two banks where emode entries can be cross-applied by shape only to `lending_pool_configure_bank_emode` so `lending_pool_configure_bank_emode` accepts a signer/object combination from the wrong group or bank, violating `emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions` and causing `Critical: public privilege escalation causing unsafe borrowing or liquidations`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_emode.rs` / `lending_pool_configure_bank_emode`
- Entrypoint: `lending_pool_configure_bank_emode`
- Attacker controls: two banks where emode entries can be cross-applied by shape only
- Exploit idea: Probe whether role checks bind authority only to the signer and not also to the exact target object being mutated. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions
- Expected Immunefi impact: Critical: public privilege escalation causing unsafe borrowing or liquidations
- Fast validation: Create multiple groups/banks and assert authorized keys for one context cannot mutate another context through shared struct shape. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
