# Q1598: get_pending_checkpoints capability or authenticator flow bypass

## Question
Can an unprivileged attacker reach `get_pending_checkpoints` with crafted last and bypass a deny-list, capability, authenticator, or authority check so protected execution proceeds anyway?

## Target
- File/function: crates/sui-core/src/authority/authority_per_epoch_store.rs::get_pending_checkpoints
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: last
- Exploit idea: Target precondition ordering, wrapper objects, stale capability state, and alternate call paths that may skip the intended authorization gate.
- Invariant to test: Protected execution flows must enforce the same authorization boundary on every reachable path before any state-changing effect occurs.
- Expected Immunefi impact: Critical or Medium depending on whether the bypass leads to direct fund movement or harmful smart-contract behavior only.
- Fast validation: Locally invoke alternate call paths with stale or mismatched capability state and see whether protected effects still occur.
