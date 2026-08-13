# Q2996: configure: delegate-role semantics differ across sibling config paths [two-groups-whose-metadata-admin] [rollback]

## Question
Can an unprivileged attacker use `marginfi_group_configure` with two groups whose metadata/admin fields can be cross-wired so `configure` reaches a sibling configuration effect through the wrong delegate role, violating `group-level delegate and admin updates must require the exact intended role and target the exact intended group only` and causing `Critical: privilege escalation to rewrite live protocol configuration`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure.rs` / `configure`
- Entrypoint: `marginfi_group_configure`
- Attacker controls: two groups whose metadata/admin fields can be cross-wired
- Exploit idea: Compare admin/curve/limit/flow/metadata/risk/emode role assumptions across related configuration entrypoints. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: group-level delegate and admin updates must require the exact intended role and target the exact intended group only
- Expected Immunefi impact: Critical: privilege escalation to rewrite live protocol configuration
- Fast validation: Attempt each delegate signer against every related path and assert only the intended fields are mutable per role. Force the late failure branch and assert every protected field fully rolls back.
