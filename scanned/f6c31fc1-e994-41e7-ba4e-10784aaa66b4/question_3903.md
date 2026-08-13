# Q3903: lending_pool_configure_bank_interest_only: delegate-role semantics differ across sibling config paths [adjacent-use-of-the-limits] [cross-object]

## Question
Can an unprivileged attacker use `lending_pool_configure_bank_interest_only` with adjacent use of the limits-only sibling path for comparison so `lending_pool_configure_bank_interest_only` reaches a sibling configuration effect through the wrong delegate role, violating `interest-only config paths must remain exact-role-bound and cannot mutate live rate assumptions through a public bypass` and causing `High: live misconfiguration enabling value extraction or protocol loss`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank_lite.rs` / `lending_pool_configure_bank_interest_only`
- Entrypoint: `lending_pool_configure_bank_interest_only`
- Attacker controls: adjacent use of the limits-only sibling path for comparison
- Exploit idea: Compare admin/curve/limit/flow/metadata/risk/emode role assumptions across related configuration entrypoints. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: interest-only config paths must remain exact-role-bound and cannot mutate live rate assumptions through a public bypass
- Expected Immunefi impact: High: live misconfiguration enabling value extraction or protocol loss
- Fast validation: Attempt each delegate signer against every related path and assert only the intended fields are mutable per role. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
