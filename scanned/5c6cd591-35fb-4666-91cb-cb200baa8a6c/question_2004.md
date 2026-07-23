# Q2004: try_acquire_owned_object_locks_post_consensus shared-object or epoch-state race

## Question
Can an unprivileged attacker drive `try_acquire_owned_object_locks_post_consensus` with crafted owned_object_refs, tx_digest, current_commit_locks, existing_locks across contention, epoch boundaries, or version races so shared state resolves differently than ownership or locking logic expects, enabling theft, lockup, or inconsistent finalization?

## Target
- File/function: crates/sui-core/src/authority/authority_per_epoch_store.rs::try_acquire_owned_object_locks_post_consensus
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: owned_object_refs, tx_digest, current_commit_locks, existing_locks
- Exploit idea: Probe stale version snapshots, deferred execution, and epoch-transition windows where two subsystems may disagree on the canonical shared state.
- Invariant to test: Shared objects and epoch-scoped state must resolve to one canonical version for authorization, execution, and settlement.
- Expected Immunefi impact: Critical if funds or objects move incorrectly; otherwise Medium for network instability or harmful contract behavior.
- Fast validation: Create a local contention harness that races shared-object access around checkpoint or epoch transitions and compare the resulting versions and effects.
