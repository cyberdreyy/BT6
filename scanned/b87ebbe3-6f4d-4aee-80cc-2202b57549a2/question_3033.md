# Q3033: configure: protected metadata or pause field can be rewritten by a normal user [duplicate-metas-altering-which-group] [cross-object]

## Question
Can an unprivileged attacker route `marginfi_group_configure` through `configure` with duplicate metas altering which group is interpreted as target so protected metadata/pause settings are rewritten without the intended role, violating `group-level delegate and admin updates must require the exact intended role and target the exact intended group only` and causing `Critical: privilege escalation to rewrite live protocol configuration`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure.rs` / `configure`
- Entrypoint: `marginfi_group_configure`
- Attacker controls: duplicate metas altering which group is interpreted as target
- Exploit idea: Treat metadata and pause state as security-relevant because wrong values can block user funds or enable later theft. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: group-level delegate and admin updates must require the exact intended role and target the exact intended group only
- Expected Immunefi impact: Critical: privilege escalation to rewrite live protocol configuration
- Fast validation: Attempt attacker-authored updates to protected metadata/pause fields and assert they always fail before mutation. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
