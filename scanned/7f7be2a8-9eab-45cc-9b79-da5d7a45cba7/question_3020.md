# Q3020: configure: initializer-like path can be re-entered or retargeted [a-partially-valid-config-where] [rollback]

## Question
Can an unprivileged attacker call `marginfi_group_configure` with a partially valid config where some delegate fields are None and others Some so `configure` re-initializes or retargets protected state that should be one-time or one-object-only, breaking `group-level delegate and admin updates must require the exact intended role and target the exact intended group only` and causing `Critical: privilege escalation to rewrite live protocol configuration`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure.rs` / `configure`
- Entrypoint: `marginfi_group_configure`
- Attacker controls: a partially valid config where some delegate fields are None and others Some
- Exploit idea: Audit init/copy/clone paths for idempotence and one-time-use assumptions that are not fully enforced on-chain. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: group-level delegate and admin updates must require the exact intended role and target the exact intended group only
- Expected Immunefi impact: Critical: privilege escalation to rewrite live protocol configuration
- Fast validation: Repeat initialization/clone with attacker-controlled accounts and assert already-initialized or foreign-owned state cannot be hijacked. Force the late failure branch and assert every protected field fully rolls back.
