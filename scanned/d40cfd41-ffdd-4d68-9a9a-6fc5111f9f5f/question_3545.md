# Q3545: lending_pool_configure_bank_emode: protected metadata or pause field can be rewritten by a normal user [a-config-call-combining-new] [cross-object]

## Question
Can an unprivileged attacker route `lending_pool_configure_bank_emode` through `lending_pool_configure_bank_emode` with a config call combining new emode tag and entry array edge cases so protected metadata/pause settings are rewritten without the intended role, violating `emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions` and causing `Critical: public privilege escalation causing unsafe borrowing or liquidations`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_emode.rs` / `lending_pool_configure_bank_emode`
- Entrypoint: `lending_pool_configure_bank_emode`
- Attacker controls: a config call combining new emode tag and entry array edge cases
- Exploit idea: Treat metadata and pause state as security-relevant because wrong values can block user funds or enable later theft. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions
- Expected Immunefi impact: Critical: public privilege escalation causing unsafe borrowing or liquidations
- Fast validation: Attempt attacker-authored updates to protected metadata/pause fields and assert they always fail before mutation. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
