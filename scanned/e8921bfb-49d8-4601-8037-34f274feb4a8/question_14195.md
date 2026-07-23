# Q14195: push_n capability or authenticator flow bypass

## Question
Can an unprivileged attacker reach `push_n` with crafted item, n and bypass a deny-list, capability, authenticator, or authority check so protected execution proceeds anyway?

## Target
- File/function: external-crates/move/crates/move-abstract-stack/src/lib.rs::push_n
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: item, n
- Exploit idea: Target precondition ordering, wrapper objects, stale capability state, and alternate call paths that may skip the intended authorization gate.
- Invariant to test: Protected execution flows must enforce the same authorization boundary on every reachable path before any state-changing effect occurs.
- Expected Immunefi impact: Critical or Medium depending on whether the bypass leads to direct fund movement or harmful smart-contract behavior only.
- Fast validation: Locally invoke alternate call paths with stale or mismatched capability state and see whether protected effects still occur.
