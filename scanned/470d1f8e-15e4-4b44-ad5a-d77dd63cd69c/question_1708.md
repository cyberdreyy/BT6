# Q1708: jwk_active_in_current_epoch capability or authenticator flow bypass

## Question
Can an unprivileged attacker reach `jwk_active_in_current_epoch` with crafted jwk_id, jwk and bypass a deny-list, capability, authenticator, or authority check so protected execution proceeds anyway?

## Target
- File/function: crates/sui-core/src/authority/authority_per_epoch_store.rs::jwk_active_in_current_epoch
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: jwk_id, jwk
- Exploit idea: Target precondition ordering, wrapper objects, stale capability state, and alternate call paths that may skip the intended authorization gate.
- Invariant to test: Protected execution flows must enforce the same authorization boundary on every reachable path before any state-changing effect occurs.
- Expected Immunefi impact: Critical or Medium depending on whether the bypass leads to direct fund movement or harmful smart-contract behavior only.
- Fast validation: Locally invoke alternate call paths with stale or mismatched capability state and see whether protected effects still occur.
