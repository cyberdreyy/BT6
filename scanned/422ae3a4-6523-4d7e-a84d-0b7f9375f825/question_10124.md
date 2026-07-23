# Q10124: compute_unchanged_consensus_objects shared-object or epoch-state race

## Question
Can an unprivileged attacker drive `compute_unchanged_consensus_objects` with crafted shared_objects, loaded_per_epoch_config_objects, changed_objects across contention, epoch boundaries, or version races so shared state resolves differently than ownership or locking logic expects, enabling theft, lockup, or inconsistent finalization?

## Target
- File/function: crates/sui-types/src/effects/effects_v2.rs::compute_unchanged_consensus_objects
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: shared_objects, loaded_per_epoch_config_objects, changed_objects
- Exploit idea: Probe stale version snapshots, deferred execution, and epoch-transition windows where two subsystems may disagree on the canonical shared state.
- Invariant to test: Shared objects and epoch-scoped state must resolve to one canonical version for authorization, execution, and settlement.
- Expected Immunefi impact: Critical if funds or objects move incorrectly; otherwise Medium for network instability or harmful contract behavior.
- Fast validation: Create a local contention harness that races shared-object access around checkpoint or epoch transitions and compare the resulting versions and effects.
