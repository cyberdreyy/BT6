# Q3857: lending_pool_configure_bank_interest_only: partial config application survives a later authorization failure [an-attacker-signer-with-a] [cross-object]

## Question
Can an unprivileged attacker make `lending_pool_configure_bank_interest_only` reach `lending_pool_configure_bank_interest_only` with an attacker signer with a victim bank and crafted interest config so some protected fields are applied before a later auth/binding failure, breaking `interest-only config paths must remain exact-role-bound and cannot mutate live rate assumptions through a public bypass` and leading to `High: live misconfiguration enabling value extraction or protocol loss`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank_lite.rs` / `lending_pool_configure_bank_interest_only`
- Entrypoint: `lending_pool_configure_bank_interest_only`
- Attacker controls: an attacker signer with a victim bank and crafted interest config
- Exploit idea: Check for multi-field updates where validation may be interleaved with mutation instead of fully front-loaded. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: interest-only config paths must remain exact-role-bound and cannot mutate live rate assumptions through a public bypass
- Expected Immunefi impact: High: live misconfiguration enabling value extraction or protocol loss
- Fast validation: Force the late failure branch and assert every protected field remains unchanged after rollback. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
