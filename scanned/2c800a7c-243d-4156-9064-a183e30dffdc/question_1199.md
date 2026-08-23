# Q1199: ed25519::new_ed25519_instruction_raw — nonce authority bypass

## Question
Can an unprivileged attacker, through a builtin-program instruction in a transaction from an unprivileged fee-payer, reach `ed25519::new_ed25519_instruction_raw` and advance or withdraw a durable nonce account without the nonce authority's signature, so that the invariant "nonce advance/withdraw requires the nonce authority signature" is violated, leading to Loss of Funds?

## Target
- File/function: `precompiles/src/ed25519.rs` -> `new_ed25519_instruction_raw`
- Entrypoint: a builtin-program instruction in a transaction from an unprivileged fee-payer
- Attacker controls: a nonce instruction referencing a nonce account it does not control
- Exploit idea: Advance or withdraw a durable nonce account without the nonce authority's signature.
- Invariant to test: nonce advance/withdraw requires the nonce authority signature.
- Expected Immunefi impact: Loss of Funds — Critical
- Fast validation: write a program-test submitting the builtin/precompile instruction and assert the authorization/accounting invariant holds.
