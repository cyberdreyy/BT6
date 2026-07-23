# Q17562: pop_n_operands unauthorized package upgrade path

## Question
Can an unprivileged attacker reach `pop_n_operands` during package upgrade with crafted n and bypass package authority, compatibility, or upgrade-policy checks so a package changes behavior without the legitimate owner’s authorization?

## Target
- File/function: external-crates/move/crates/move-vm-runtime/src/execution/interpreter/state.rs::pop_n_operands
- Entrypoint: Package publish or package upgrade transaction with crafted Move bytecode, metadata, or dependency state
- Attacker controls: n
- Exploit idea: Test whether upgrade capability, dependency graph, linkage state, or compatibility checks can be confused into approving an attacker-controlled package version.
- Invariant to test: Only the authorized upgrade path may change package code or linkage, and every upgrade must preserve the intended compatibility boundary.
- Expected Immunefi impact: Critical — unauthorized package upgrade leading to significant loss of funds or protected-state corruption.
- Fast validation: Build a conflicting upgrade package locally, vary dependencies and policy flags, and see whether the network accepts and executes it.
