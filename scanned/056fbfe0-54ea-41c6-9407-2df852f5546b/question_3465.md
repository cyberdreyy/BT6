# Q3465: lending_pool_configure_bank_emode: public caller bypasses role-bound configuration [a-config-call-combining-new] [cross-object]

## Question
Can an unprivileged attacker invoke `lending_pool_configure_bank_emode` with a config call combining new emode tag and entry array edge cases so `lending_pool_configure_bank_emode` applies a group/bank configuration change without the intended role, violating `emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions` and causing `Critical: public privilege escalation causing unsafe borrowing or liquidations`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_emode.rs` / `lending_pool_configure_bank_emode`
- Entrypoint: `lending_pool_configure_bank_emode`
- Attacker controls: a config call combining new emode tag and entry array edge cases
- Exploit idea: Attack every signer, delegate-role, and group/bank binding assumption on the path so configuration writes cannot be reached by a normal user. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: emode configuration must remain role-bound and bank-bound so no public caller can alter live leverage assumptions
- Expected Immunefi impact: Critical: public privilege escalation causing unsafe borrowing or liquidations
- Fast validation: Use attacker-controlled signer/accounts against the config path and assert no protected field changes unless the exact authorized role signs. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
