# Q3849: lending_pool_configure_bank_interest_only: public caller bypasses role-bound configuration [a-config-update-at-curve] [cross-object]

## Question
Can an unprivileged attacker invoke `lending_pool_configure_bank_interest_only` with a config update at curve-segment and rate-boundary values so `lending_pool_configure_bank_interest_only` applies a group/bank configuration change without the intended role, violating `interest-only config paths must remain exact-role-bound and cannot mutate live rate assumptions through a public bypass` and causing `High: live misconfiguration enabling value extraction or protocol loss`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank_lite.rs` / `lending_pool_configure_bank_interest_only`
- Entrypoint: `lending_pool_configure_bank_interest_only`
- Attacker controls: a config update at curve-segment and rate-boundary values
- Exploit idea: Attack every signer, delegate-role, and group/bank binding assumption on the path so configuration writes cannot be reached by a normal user. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: interest-only config paths must remain exact-role-bound and cannot mutate live rate assumptions through a public bypass
- Expected Immunefi impact: High: live misconfiguration enabling value extraction or protocol loss
- Fast validation: Use attacker-controlled signer/accounts against the config path and assert no protected field changes unless the exact authorized role signs. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
