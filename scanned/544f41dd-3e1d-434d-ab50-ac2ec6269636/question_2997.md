# Q2997: configure: delegate-role semantics differ across sibling config paths [a-configuration-call-that-updates] [cross-object]

## Question
Can an unprivileged attacker use `marginfi_group_configure` with a configuration call that updates multiple delegate fields at once so `configure` reaches a sibling configuration effect through the wrong delegate role, violating `group-level delegate and admin updates must require the exact intended role and target the exact intended group only` and causing `Critical: privilege escalation to rewrite live protocol configuration`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure.rs` / `configure`
- Entrypoint: `marginfi_group_configure`
- Attacker controls: a configuration call that updates multiple delegate fields at once
- Exploit idea: Compare admin/curve/limit/flow/metadata/risk/emode role assumptions across related configuration entrypoints. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: group-level delegate and admin updates must require the exact intended role and target the exact intended group only
- Expected Immunefi impact: Critical: privilege escalation to rewrite live protocol configuration
- Fast validation: Attempt each delegate signer against every related path and assert only the intended fields are mutable per role. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
