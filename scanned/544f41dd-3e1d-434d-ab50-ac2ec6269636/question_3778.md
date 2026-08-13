# Q3778: lending_pool_configure_bank: initializer-like path can be re-entered or retargeted [an-attacker-signer-with-a] [rollback]

## Question
Can an unprivileged attacker call `lending_pool_configure_bank` with an attacker signer with a victim bank and crafted config update so `lending_pool_configure_bank` re-initializes or retargets protected state that should be one-time or one-object-only, breaking `full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only` and causing `Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs` / `lending_pool_configure_bank`
- Entrypoint: `lending_pool_configure_bank`
- Attacker controls: an attacker signer with a victim bank and crafted config update
- Exploit idea: Audit init/copy/clone paths for idempotence and one-time-use assumptions that are not fully enforced on-chain. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only
- Expected Immunefi impact: Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt
- Fast validation: Repeat initialization/clone with attacker-controlled accounts and assert already-initialized or foreign-owned state cannot be hijacked. Force the late failure branch and assert every protected field fully rolls back.
