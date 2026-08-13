# Q3816: lending_pool_configure_bank: config path trusts caller-chosen remaining accounts too much [same-slot-bank-config-attempt] [rollback]

## Question
Can an unprivileged attacker use `lending_pool_configure_bank` with same-slot bank-config attempt before user borrow/withdraw investigations so `lending_pool_configure_bank` applies a protected configuration change using caller-chosen auxiliary accounts, violating `full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only` and leading to `Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs` / `lending_pool_configure_bank`
- Entrypoint: `lending_pool_configure_bank`
- Attacker controls: same-slot bank-config attempt before user borrow/withdraw investigations
- Exploit idea: Look for config flows that pull oracle, metadata, or derived objects from remaining accounts without fully binding them. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only
- Expected Immunefi impact: Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt
- Fast validation: Swap candidate auxiliary accounts and assert the config path cannot mutate anything unless every auxiliary object matches the canonical target. Force the late failure branch and assert every protected field fully rolls back.
