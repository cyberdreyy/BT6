# Q3914: lending_pool_configure_bank_interest_only: initializer-like path can be re-entered or retargeted [a-config-update-at-curve] [rollback]

## Question
Can an unprivileged attacker call `lending_pool_configure_bank_interest_only` with a config update at curve-segment and rate-boundary values so `lending_pool_configure_bank_interest_only` re-initializes or retargets protected state that should be one-time or one-object-only, breaking `interest-only config paths must remain exact-role-bound and cannot mutate live rate assumptions through a public bypass` and causing `High: live misconfiguration enabling value extraction or protocol loss`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank_lite.rs` / `lending_pool_configure_bank_interest_only`
- Entrypoint: `lending_pool_configure_bank_interest_only`
- Attacker controls: a config update at curve-segment and rate-boundary values
- Exploit idea: Audit init/copy/clone paths for idempotence and one-time-use assumptions that are not fully enforced on-chain. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: interest-only config paths must remain exact-role-bound and cannot mutate live rate assumptions through a public bypass
- Expected Immunefi impact: High: live misconfiguration enabling value extraction or protocol loss
- Fast validation: Repeat initialization/clone with attacker-controlled accounts and assert already-initialized or foreign-owned state cannot be hijacked. Force the late failure branch and assert every protected field fully rolls back.
