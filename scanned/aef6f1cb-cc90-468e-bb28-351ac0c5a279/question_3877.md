# Q3877: lending_pool_configure_bank_interest_only: cross-group or cross-bank object passes role checks [same-slot-config-attempt-before] [cross-object]

## Question
Can an unprivileged attacker supply same-slot config attempt before public accrual or borrow investigations to `lending_pool_configure_bank_interest_only` so `lending_pool_configure_bank_interest_only` accepts a signer/object combination from the wrong group or bank, violating `interest-only config paths must remain exact-role-bound and cannot mutate live rate assumptions through a public bypass` and causing `High: live misconfiguration enabling value extraction or protocol loss`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank_lite.rs` / `lending_pool_configure_bank_interest_only`
- Entrypoint: `lending_pool_configure_bank_interest_only`
- Attacker controls: same-slot config attempt before public accrual or borrow investigations
- Exploit idea: Probe whether role checks bind authority only to the signer and not also to the exact target object being mutated. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: interest-only config paths must remain exact-role-bound and cannot mutate live rate assumptions through a public bypass
- Expected Immunefi impact: High: live misconfiguration enabling value extraction or protocol loss
- Fast validation: Create multiple groups/banks and assert authorized keys for one context cannot mutate another context through shared struct shape. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
