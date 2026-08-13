# Q3059: configure: clone or copy helper can duplicate privileged state into the wrong object [two-groups-whose-metadata-admin] [cross-object]

## Question
Can an unprivileged attacker make `marginfi_group_configure` reach `configure` with two groups whose metadata/admin fields can be cross-wired so protected state is cloned or copied into the wrong destination, violating `group-level delegate and admin updates must require the exact intended role and target the exact intended group only` and causing `Critical: privilege escalation to rewrite live protocol configuration`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure.rs` / `configure`
- Entrypoint: `marginfi_group_configure`
- Attacker controls: two groups whose metadata/admin fields can be cross-wired
- Exploit idea: Attack any helper that duplicates config, fee state, emode, or metadata across objects and must bind source and destination tightly. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: group-level delegate and admin updates must require the exact intended role and target the exact intended group only
- Expected Immunefi impact: Critical: privilege escalation to rewrite live protocol configuration
- Fast validation: Use mixed-validity source/destination objects and assert no protected state lands on an attacker-selected destination. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
