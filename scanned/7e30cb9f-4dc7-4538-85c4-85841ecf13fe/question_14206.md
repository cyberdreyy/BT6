# Q14206: legacy shared-object or epoch-state race

## Question
Can an unprivileged attacker drive `legacy` with crafted max_binary_format_version, min_binary_format_version, check_no_extraneous_bytes, deprecate_global_storage_ops across contention, epoch boundaries, or version races so shared state resolves differently than ownership or locking logic expects, enabling theft, lockup, or inconsistent finalization?

## Target
- File/function: external-crates/move/crates/move-binary-format/src/binary_config.rs::legacy
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: max_binary_format_version, min_binary_format_version, check_no_extraneous_bytes, deprecate_global_storage_ops
- Exploit idea: Probe stale version snapshots, deferred execution, and epoch-transition windows where two subsystems may disagree on the canonical shared state.
- Invariant to test: Shared objects and epoch-scoped state must resolve to one canonical version for authorization, execution, and settlement.
- Expected Immunefi impact: Critical if funds or objects move incorrectly; otherwise Medium for network instability or harmful contract behavior.
- Fast validation: Create a local contention harness that races shared-object access around checkpoint or epoch transitions and compare the resulting versions and effects.
