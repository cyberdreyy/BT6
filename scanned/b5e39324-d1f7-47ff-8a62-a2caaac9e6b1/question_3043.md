# Q3043: configure: config path trusts caller-chosen remaining accounts too much [two-groups-whose-metadata-admin] [cross-object]

## Question
Can an unprivileged attacker use `marginfi_group_configure` with two groups whose metadata/admin fields can be cross-wired so `configure` applies a protected configuration change using caller-chosen auxiliary accounts, violating `group-level delegate and admin updates must require the exact intended role and target the exact intended group only` and leading to `Critical: privilege escalation to rewrite live protocol configuration`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure.rs` / `configure`
- Entrypoint: `marginfi_group_configure`
- Attacker controls: two groups whose metadata/admin fields can be cross-wired
- Exploit idea: Look for config flows that pull oracle, metadata, or derived objects from remaining accounts without fully binding them. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: group-level delegate and admin updates must require the exact intended role and target the exact intended group only
- Expected Immunefi impact: Critical: privilege escalation to rewrite live protocol configuration
- Fast validation: Swap candidate auxiliary accounts and assert the config path cannot mutate anything unless every auxiliary object matches the canonical target. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
