# Q3942: lending_pool_configure_bank_interest_only: config path trusts caller-chosen remaining accounts too much [same-slot-config-attempt-before] [rollback]

## Question
Can an unprivileged attacker use `lending_pool_configure_bank_interest_only` with same-slot config attempt before public accrual or borrow investigations so `lending_pool_configure_bank_interest_only` applies a protected configuration change using caller-chosen auxiliary accounts, violating `interest-only config paths must remain exact-role-bound and cannot mutate live rate assumptions through a public bypass` and leading to `High: live misconfiguration enabling value extraction or protocol loss`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank_lite.rs` / `lending_pool_configure_bank_interest_only`
- Entrypoint: `lending_pool_configure_bank_interest_only`
- Attacker controls: same-slot config attempt before public accrual or borrow investigations
- Exploit idea: Look for config flows that pull oracle, metadata, or derived objects from remaining accounts without fully binding them. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: interest-only config paths must remain exact-role-bound and cannot mutate live rate assumptions through a public bypass
- Expected Immunefi impact: High: live misconfiguration enabling value extraction or protocol loss
- Fast validation: Swap candidate auxiliary accounts and assert the config path cannot mutate anything unless every auxiliary object matches the canonical target. Force the late failure branch and assert every protected field fully rolls back.
