# Q11335: is_last_checkpoint_of_epoch shared-object or epoch-state race

## Question
Can an unprivileged attacker drive `is_last_checkpoint_of_epoch` with crafted signature bytes, authenticator payloads, digests, object references, and type data across contention, epoch boundaries, or version races so shared state resolves differently than ownership or locking logic expects, enabling theft, lockup, or inconsistent finalization?

## Target
- File/function: crates/sui-types/src/messages_checkpoint.rs::is_last_checkpoint_of_epoch
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: signature bytes, authenticator payloads, digests, object references, and type data
- Exploit idea: Probe stale version snapshots, deferred execution, and epoch-transition windows where two subsystems may disagree on the canonical shared state.
- Invariant to test: Shared objects and epoch-scoped state must resolve to one canonical version for authorization, execution, and settlement.
- Expected Immunefi impact: Critical if funds or objects move incorrectly; otherwise Medium for network instability or harmful contract behavior.
- Fast validation: Create a local contention harness that races shared-object access around checkpoint or epoch transitions and compare the resulting versions and effects.
