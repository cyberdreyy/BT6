# Q947: vote_processor::process_instruction_as_one_arg — nonce authority bypass

## Question
Can an unprivileged attacker, through a builtin-program instruction in a transaction from an unprivileged fee-payer, reach `vote_processor::process_instruction_as_one_arg` and advance or withdraw a durable nonce account without the nonce authority's signature, so that the invariant "nonce advance/withdraw requires the nonce authority signature" is violated, leading to Loss of Funds?

## Target
- File/function: `programs/vote/src/vote_processor.rs` -> `process_instruction_as_one_arg`
- Entrypoint: a builtin-program instruction in a transaction from an unprivileged fee-payer
- Attacker controls: a nonce instruction referencing a nonce account it does not control
- Exploit idea: Advance or withdraw a durable nonce account without the nonce authority's signature.
- Invariant to test: nonce advance/withdraw requires the nonce authority signature.
- Expected Immunefi impact: Loss of Funds — Critical
- Fast validation: write a program-test submitting the builtin/precompile instruction and assert the authorization/accounting invariant holds.
