# Q15710: from_hex_literal capability or authenticator flow bypass

## Question
Can an unprivileged attacker reach `from_hex_literal` with crafted literal and bypass a deny-list, capability, authenticator, or authority check so protected execution proceeds anyway?

## Target
- File/function: external-crates/move/crates/move-core-types/src/account_address.rs::from_hex_literal
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: literal
- Exploit idea: Target precondition ordering, wrapper objects, stale capability state, and alternate call paths that may skip the intended authorization gate.
- Invariant to test: Protected execution flows must enforce the same authorization boundary on every reachable path before any state-changing effect occurs.
- Expected Immunefi impact: Critical or Medium depending on whether the bypass leads to direct fund movement or harmful smart-contract behavior only.
- Fast validation: Locally invoke alternate call paths with stale or mismatched capability state and see whether protected effects still occur.
