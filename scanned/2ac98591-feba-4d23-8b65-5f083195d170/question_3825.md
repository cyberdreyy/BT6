# Q3825: lending_pool_configure_bank: clone or copy helper can duplicate privileged state into the wrong object [an-attacker-signer-with-a] [cross-object]

## Question
Can an unprivileged attacker make `lending_pool_configure_bank` reach `lending_pool_configure_bank` with an attacker signer with a victim bank and crafted config update so protected state is cloned or copied into the wrong destination, violating `full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only` and causing `Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs` / `lending_pool_configure_bank`
- Entrypoint: `lending_pool_configure_bank`
- Attacker controls: an attacker signer with a victim bank and crafted config update
- Exploit idea: Attack any helper that duplicates config, fee state, emode, or metadata across objects and must bind source and destination tightly. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only
- Expected Immunefi impact: Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt
- Fast validation: Use mixed-validity source/destination objects and assert no protected state lands on an attacker-selected destination. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
