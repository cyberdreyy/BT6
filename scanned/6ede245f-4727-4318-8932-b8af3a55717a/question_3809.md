# Q3809: lending_pool_configure_bank: config path trusts caller-chosen remaining accounts too much [an-attacker-signer-with-a] [cross-object]

## Question
Can an unprivileged attacker use `lending_pool_configure_bank` with an attacker signer with a victim bank and crafted config update so `lending_pool_configure_bank` applies a protected configuration change using caller-chosen auxiliary accounts, violating `full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only` and leading to `Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs` / `lending_pool_configure_bank`
- Entrypoint: `lending_pool_configure_bank`
- Attacker controls: an attacker signer with a victim bank and crafted config update
- Exploit idea: Look for config flows that pull oracle, metadata, or derived objects from remaining accounts without fully binding them. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only
- Expected Immunefi impact: Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt
- Fast validation: Swap candidate auxiliary accounts and assert the config path cannot mutate anything unless every auxiliary object matches the canonical target. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
