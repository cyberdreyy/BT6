# Q1220: prepare_certificate_for_benchmark capability or authenticator flow bypass

## Question
Can an unprivileged attacker reach `prepare_certificate_for_benchmark` with crafted certificate, input_objects, epoch_store and bypass a deny-list, capability, authenticator, or authority check so protected execution proceeds anyway?

## Target
- File/function: crates/sui-core/src/authority.rs::prepare_certificate_for_benchmark
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: certificate, input_objects, epoch_store
- Exploit idea: Target precondition ordering, wrapper objects, stale capability state, and alternate call paths that may skip the intended authorization gate.
- Invariant to test: Protected execution flows must enforce the same authorization boundary on every reachable path before any state-changing effect occurs.
- Expected Immunefi impact: Critical or Medium depending on whether the bypass leads to direct fund movement or harmful smart-contract behavior only.
- Fast validation: Locally invoke alternate call paths with stale or mismatched capability state and see whether protected effects still occur.
