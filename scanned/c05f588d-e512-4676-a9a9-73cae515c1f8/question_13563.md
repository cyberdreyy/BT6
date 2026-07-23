# Q13563: new_authenticator_state_expire shared-object or epoch-state race

## Question
Can an unprivileged attacker drive `new_authenticator_state_expire` with crafted min_epoch, authenticator_obj_initial_shared_version across contention, epoch boundaries, or version races so shared state resolves differently than ownership or locking logic expects, enabling theft, lockup, or inconsistent finalization?

## Target
- File/function: crates/sui-types/src/transaction.rs::new_authenticator_state_expire
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: min_epoch, authenticator_obj_initial_shared_version
- Exploit idea: Probe stale version snapshots, deferred execution, and epoch-transition windows where two subsystems may disagree on the canonical shared state.
- Invariant to test: Shared objects and epoch-scoped state must resolve to one canonical version for authorization, execution, and settlement.
- Expected Immunefi impact: Critical if funds or objects move incorrectly; otherwise Medium for network instability or harmful contract behavior.
- Fast validation: Create a local contention harness that races shared-object access around checkpoint or epoch transitions and compare the resulting versions and effects.
