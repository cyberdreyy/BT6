# Q2956: configure: public caller bypasses role-bound configuration [a-partially-valid-config-where] [rollback]

## Question
Can an unprivileged attacker invoke `marginfi_group_configure` with a partially valid config where some delegate fields are None and others Some so `configure` applies a group/bank configuration change without the intended role, violating `group-level delegate and admin updates must require the exact intended role and target the exact intended group only` and causing `Critical: privilege escalation to rewrite live protocol configuration`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure.rs` / `configure`
- Entrypoint: `marginfi_group_configure`
- Attacker controls: a partially valid config where some delegate fields are None and others Some
- Exploit idea: Attack every signer, delegate-role, and group/bank binding assumption on the path so configuration writes cannot be reached by a normal user. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: group-level delegate and admin updates must require the exact intended role and target the exact intended group only
- Expected Immunefi impact: Critical: privilege escalation to rewrite live protocol configuration
- Fast validation: Use attacker-controlled signer/accounts against the config path and assert no protected field changes unless the exact authorized role signs. Force the late failure branch and assert every protected field fully rolls back.
