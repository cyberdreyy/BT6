# Q3497: lending_pool_configure_bank_emode: cross-group or cross-bank object passes role checks [a-config-call-combining-new] [cross-object]

## Question
Can an unprivileged attacker supply a config call combining new emode tag and entry array edge cases to `lending_pool_configure_bank_emode` so `lending_pool_configure_bank_emode` accepts a signer/object combination from the wrong group or bank, violating `emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions` and causing `Critical: public privilege escalation causing unsafe borrowing or liquidations`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_emode.rs` / `lending_pool_configure_bank_emode`
- Entrypoint: `lending_pool_configure_bank_emode`
- Attacker controls: a config call combining new emode tag and entry array edge cases
- Exploit idea: Probe whether role checks bind authority only to the signer and not also to the exact target object being mutated. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions
- Expected Immunefi impact: Critical: public privilege escalation causing unsafe borrowing or liquidations
- Fast validation: Create multiple groups/banks and assert authorized keys for one context cannot mutate another context through shared struct shape. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
