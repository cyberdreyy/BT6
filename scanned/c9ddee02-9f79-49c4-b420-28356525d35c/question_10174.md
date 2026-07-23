# Q10174: new_from_execution_v1 shared-object or epoch-state race

## Question
Can an unprivileged attacker drive `new_from_execution_v1` with crafted status, executed_epoch, gas_used, modified_at_versions across contention, epoch boundaries, or version races so shared state resolves differently than ownership or locking logic expects, enabling theft, lockup, or inconsistent finalization?

## Target
- File/function: crates/sui-types/src/effects/mod.rs::new_from_execution_v1
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: status, executed_epoch, gas_used, modified_at_versions
- Exploit idea: Probe stale version snapshots, deferred execution, and epoch-transition windows where two subsystems may disagree on the canonical shared state.
- Invariant to test: Shared objects and epoch-scoped state must resolve to one canonical version for authorization, execution, and settlement.
- Expected Immunefi impact: Critical if funds or objects move incorrectly; otherwise Medium for network instability or harmful contract behavior.
- Fast validation: Create a local contention harness that races shared-object access around checkpoint or epoch transitions and compare the resulting versions and effects.
