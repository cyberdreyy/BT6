# Q3795: lending_pool_configure_bank: protected metadata or pause field can be rewritten by a normal user [two-banks-whose-configs-can] [cross-object]

## Question
Can an unprivileged attacker route `lending_pool_configure_bank` through `lending_pool_configure_bank` with two banks whose configs can be cross-wired so protected metadata/pause settings are rewritten without the intended role, violating `full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only` and causing `Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs` / `lending_pool_configure_bank`
- Entrypoint: `lending_pool_configure_bank`
- Attacker controls: two banks whose configs can be cross-wired
- Exploit idea: Treat metadata and pause state as security-relevant because wrong values can block user funds or enable later theft. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only
- Expected Immunefi impact: Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt
- Fast validation: Attempt attacker-authored updates to protected metadata/pause fields and assert they always fail before mutation. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
