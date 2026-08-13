# Q3928: lending_pool_configure_bank_interest_only: protected metadata or pause field can be rewritten by a normal user [duplicate-metas-altering-target-bank] [rollback]

## Question
Can an unprivileged attacker route `lending_pool_configure_bank_interest_only` through `lending_pool_configure_bank_interest_only` with duplicate metas altering target-bank interpretation so protected metadata/pause settings are rewritten without the intended role, violating `interest-only config paths must remain exact-role-bound and cannot mutate live rate assumptions through a public bypass` and causing `High: live misconfiguration enabling value extraction or protocol loss`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank_lite.rs` / `lending_pool_configure_bank_interest_only`
- Entrypoint: `lending_pool_configure_bank_interest_only`
- Attacker controls: duplicate metas altering target-bank interpretation
- Exploit idea: Treat metadata and pause state as security-relevant because wrong values can block user funds or enable later theft. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: interest-only config paths must remain exact-role-bound and cannot mutate live rate assumptions through a public bypass
- Expected Immunefi impact: High: live misconfiguration enabling value extraction or protocol loss
- Fast validation: Attempt attacker-authored updates to protected metadata/pause fields and assert they always fail before mutation. Force the late failure branch and assert every protected field fully rolls back.
