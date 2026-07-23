# Q2415: set_highest_pruned_checkpoint capability or authenticator flow bypass

## Question
Can an unprivileged attacker reach `set_highest_pruned_checkpoint` with crafted wb, checkpoint_number and bypass a deny-list, capability, authenticator, or authority check so protected execution proceeds anyway?

## Target
- File/function: crates/sui-core/src/authority/authority_store_tables.rs::set_highest_pruned_checkpoint
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: wb, checkpoint_number
- Exploit idea: Target precondition ordering, wrapper objects, stale capability state, and alternate call paths that may skip the intended authorization gate.
- Invariant to test: Protected execution flows must enforce the same authorization boundary on every reachable path before any state-changing effect occurs.
- Expected Immunefi impact: Critical or Medium depending on whether the bypass leads to direct fund movement or harmful smart-contract behavior only.
- Fast validation: Locally invoke alternate call paths with stale or mismatched capability state and see whether protected effects still occur.
