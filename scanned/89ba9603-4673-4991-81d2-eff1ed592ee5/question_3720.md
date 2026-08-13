# Q3720: lending_pool_configure_bank: public caller bypasses role-bound configuration [same-slot-bank-config-attempt] [rollback]

## Question
Can an unprivileged attacker invoke `lending_pool_configure_bank` with same-slot bank-config attempt before user borrow/withdraw investigations so `lending_pool_configure_bank` applies a group/bank configuration change without the intended role, violating `full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only` and causing `Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs` / `lending_pool_configure_bank`
- Entrypoint: `lending_pool_configure_bank`
- Attacker controls: same-slot bank-config attempt before user borrow/withdraw investigations
- Exploit idea: Attack every signer, delegate-role, and group/bank binding assumption on the path so configuration writes cannot be reached by a normal user. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only
- Expected Immunefi impact: Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt
- Fast validation: Use attacker-controlled signer/accounts against the config path and assert no protected field changes unless the exact authorized role signs. Force the late failure branch and assert every protected field fully rolls back.
