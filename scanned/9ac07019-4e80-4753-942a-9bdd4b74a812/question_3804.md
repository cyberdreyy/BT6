# Q3804: lending_pool_configure_bank: protected metadata or pause field can be rewritten by a normal user [a-partially-populated-config-opt] [rollback]

## Question
Can an unprivileged attacker route `lending_pool_configure_bank` through `lending_pool_configure_bank` with a partially populated config opt object with boundary values so protected metadata/pause settings are rewritten without the intended role, violating `full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only` and causing `Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs` / `lending_pool_configure_bank`
- Entrypoint: `lending_pool_configure_bank`
- Attacker controls: a partially populated config opt object with boundary values
- Exploit idea: Treat metadata and pause state as security-relevant because wrong values can block user funds or enable later theft. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only
- Expected Immunefi impact: Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt
- Fast validation: Attempt attacker-authored updates to protected metadata/pause fields and assert they always fail before mutation. Force the late failure branch and assert every protected field fully rolls back.
