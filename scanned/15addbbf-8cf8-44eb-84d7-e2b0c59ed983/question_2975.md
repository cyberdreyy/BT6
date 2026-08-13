# Q2975: configure: partial config application survives a later authorization failure [emode-leverage-updates-combined-with] [cross-object]

## Question
Can an unprivileged attacker make `marginfi_group_configure` reach `configure` with emode leverage updates combined with authority-field changes so some protected fields are applied before a later auth/binding failure, breaking `group-level delegate and admin updates must require the exact intended role and target the exact intended group only` and leading to `Critical: privilege escalation to rewrite live protocol configuration`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure.rs` / `configure`
- Entrypoint: `marginfi_group_configure`
- Attacker controls: emode leverage updates combined with authority-field changes
- Exploit idea: Check for multi-field updates where validation may be interleaved with mutation instead of fully front-loaded. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: group-level delegate and admin updates must require the exact intended role and target the exact intended group only
- Expected Immunefi impact: Critical: privilege escalation to rewrite live protocol configuration
- Fast validation: Force the late failure branch and assert every protected field remains unchanged after rollback. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
