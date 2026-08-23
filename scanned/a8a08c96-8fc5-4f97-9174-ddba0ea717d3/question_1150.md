# Q1150: handler::default_v4 — nonce authority bypass

## Question
Can an unprivileged attacker, through a builtin-program instruction in a transaction from an unprivileged fee-payer, reach `handler::default_v4` and advance or withdraw a durable nonce account without the nonce authority's signature, so that the invariant "nonce advance/withdraw requires the nonce authority signature" is violated, leading to Loss of Funds?

## Target
- File/function: `programs/vote/src/vote_state/handler.rs` -> `default_v4`
- Entrypoint: a builtin-program instruction in a transaction from an unprivileged fee-payer
- Attacker controls: a nonce instruction referencing a nonce account it does not control
- Exploit idea: Advance or withdraw a durable nonce account without the nonce authority's signature.
- Invariant to test: nonce advance/withdraw requires the nonce authority signature.
- Expected Immunefi impact: Loss of Funds — Critical
- Fast validation: write a program-test submitting the builtin/precompile instruction and assert the authorization/accounting invariant holds.
