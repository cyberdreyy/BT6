# Q3864: lending_pool_configure_bank_interest_only: partial config application survives a later authorization failure [duplicate-metas-altering-target-bank] [rollback]

## Question
Can an unprivileged attacker make `lending_pool_configure_bank_interest_only` reach `lending_pool_configure_bank_interest_only` with duplicate metas altering target-bank interpretation so some protected fields are applied before a later auth/binding failure, breaking `interest-only config paths must remain exact-role-bound and cannot mutate live rate assumptions through a public bypass` and leading to `High: live misconfiguration enabling value extraction or protocol loss`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank_lite.rs` / `lending_pool_configure_bank_interest_only`
- Entrypoint: `lending_pool_configure_bank_interest_only`
- Attacker controls: duplicate metas altering target-bank interpretation
- Exploit idea: Check for multi-field updates where validation may be interleaved with mutation instead of fully front-loaded. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: interest-only config paths must remain exact-role-bound and cannot mutate live rate assumptions through a public bypass
- Expected Immunefi impact: High: live misconfiguration enabling value extraction or protocol loss
- Fast validation: Force the late failure branch and assert every protected field remains unchanged after rollback. Force the late failure branch and assert every protected field fully rolls back.
