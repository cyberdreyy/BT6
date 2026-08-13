# Q3739: lending_pool_configure_bank: partial config application survives a later authorization failure [a-partially-populated-config-opt] [cross-object]

## Question
Can an unprivileged attacker make `lending_pool_configure_bank` reach `lending_pool_configure_bank` with a partially populated config opt object with boundary values so some protected fields are applied before a later auth/binding failure, breaking `full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only` and leading to `Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs` / `lending_pool_configure_bank`
- Entrypoint: `lending_pool_configure_bank`
- Attacker controls: a partially populated config opt object with boundary values
- Exploit idea: Check for multi-field updates where validation may be interleaved with mutation instead of fully front-loaded. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only
- Expected Immunefi impact: Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt
- Fast validation: Force the late failure branch and assert every protected field remains unchanged after rollback. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
