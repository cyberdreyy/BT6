# Q3968: lending_pool_configure_bank_interest_only: clone or copy helper can duplicate privileged state into the wrong object [adjacent-use-of-the-limits] [rollback]

## Question
Can an unprivileged attacker make `lending_pool_configure_bank_interest_only` reach `lending_pool_configure_bank_interest_only` with adjacent use of the limits-only sibling path for comparison so protected state is cloned or copied into the wrong destination, violating `interest-only config paths must remain exact-role-bound and cannot mutate live rate assumptions through a public bypass` and causing `High: live misconfiguration enabling value extraction or protocol loss`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank_lite.rs` / `lending_pool_configure_bank_interest_only`
- Entrypoint: `lending_pool_configure_bank_interest_only`
- Attacker controls: adjacent use of the limits-only sibling path for comparison
- Exploit idea: Attack any helper that duplicates config, fee state, emode, or metadata across objects and must bind source and destination tightly. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: interest-only config paths must remain exact-role-bound and cannot mutate live rate assumptions through a public bypass
- Expected Immunefi impact: High: live misconfiguration enabling value extraction or protocol loss
- Fast validation: Use mixed-validity source/destination objects and assert no protected state lands on an attacker-selected destination. Force the late failure branch and assert every protected field fully rolls back.
