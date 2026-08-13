# Q2964: configure: partial config application survives a later authorization failure [two-groups-whose-metadata-admin] [rollback]

## Question
Can an unprivileged attacker make `marginfi_group_configure` reach `configure` with two groups whose metadata/admin fields can be cross-wired so some protected fields are applied before a later auth/binding failure, breaking `group-level delegate and admin updates must require the exact intended role and target the exact intended group only` and leading to `Critical: privilege escalation to rewrite live protocol configuration`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure.rs` / `configure`
- Entrypoint: `marginfi_group_configure`
- Attacker controls: two groups whose metadata/admin fields can be cross-wired
- Exploit idea: Check for multi-field updates where validation may be interleaved with mutation instead of fully front-loaded. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: group-level delegate and admin updates must require the exact intended role and target the exact intended group only
- Expected Immunefi impact: Critical: privilege escalation to rewrite live protocol configuration
- Fast validation: Force the late failure branch and assert every protected field remains unchanged after rollback. Force the late failure branch and assert every protected field fully rolls back.
