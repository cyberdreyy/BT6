# Q3038: configure: protected metadata or pause field can be rewritten by a normal user [replay-of-a-previously-valid] [rollback]

## Question
Can an unprivileged attacker route `marginfi_group_configure` through `configure` with replay of a previously valid config layout under a new signer so protected metadata/pause settings are rewritten without the intended role, violating `group-level delegate and admin updates must require the exact intended role and target the exact intended group only` and causing `Critical: privilege escalation to rewrite live protocol configuration`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure.rs` / `configure`
- Entrypoint: `marginfi_group_configure`
- Attacker controls: replay of a previously valid config layout under a new signer
- Exploit idea: Treat metadata and pause state as security-relevant because wrong values can block user funds or enable later theft. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: group-level delegate and admin updates must require the exact intended role and target the exact intended group only
- Expected Immunefi impact: Critical: privilege escalation to rewrite live protocol configuration
- Fast validation: Attempt attacker-authored updates to protected metadata/pause fields and assert they always fail before mutation. Force the late failure branch and assert every protected field fully rolls back.
