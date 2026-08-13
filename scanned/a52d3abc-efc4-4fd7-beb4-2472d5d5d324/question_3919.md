# Q3919: lending_pool_configure_bank_interest_only: initializer-like path can be re-entered or retargeted [adjacent-use-of-the-limits] [cross-object]

## Question
Can an unprivileged attacker call `lending_pool_configure_bank_interest_only` with adjacent use of the limits-only sibling path for comparison so `lending_pool_configure_bank_interest_only` re-initializes or retargets protected state that should be one-time or one-object-only, breaking `interest-only config paths must remain exact-role-bound and cannot mutate live rate assumptions through a public bypass` and causing `High: live misconfiguration enabling value extraction or protocol loss`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank_lite.rs` / `lending_pool_configure_bank_interest_only`
- Entrypoint: `lending_pool_configure_bank_interest_only`
- Attacker controls: adjacent use of the limits-only sibling path for comparison
- Exploit idea: Audit init/copy/clone paths for idempotence and one-time-use assumptions that are not fully enforced on-chain. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: interest-only config paths must remain exact-role-bound and cannot mutate live rate assumptions through a public bypass
- Expected Immunefi impact: High: live misconfiguration enabling value extraction or protocol loss
- Fast validation: Repeat initialization/clone with attacker-controlled accounts and assert already-initialized or foreign-owned state cannot be hijacked. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
