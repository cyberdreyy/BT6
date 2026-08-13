# Q3734: lending_pool_configure_bank: partial config application survives a later authorization failure [a-config-update-that-changes] [rollback]

## Question
Can an unprivileged attacker make `lending_pool_configure_bank` reach `lending_pool_configure_bank` with a config update that changes many safety-critical fields simultaneously so some protected fields are applied before a later auth/binding failure, breaking `full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only` and leading to `Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs` / `lending_pool_configure_bank`
- Entrypoint: `lending_pool_configure_bank`
- Attacker controls: a config update that changes many safety-critical fields simultaneously
- Exploit idea: Check for multi-field updates where validation may be interleaved with mutation instead of fully front-loaded. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only
- Expected Immunefi impact: Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt
- Fast validation: Force the late failure branch and assert every protected field remains unchanged after rollback. Force the late failure branch and assert every protected field fully rolls back.
