# Q920: system_instruction::verify_nonce_ok — vote state authorization

## Question
Can an unprivileged attacker, through a builtin-program instruction in a transaction from an unprivileged fee-payer, reach `system_instruction::verify_nonce_ok` and update vote-account authority, commission or state without the vote authority's signature, so that the invariant "vote-account mutations require the correct authorized signer" is violated, leading to Loss of Funds / Consensus?

## Target
- File/function: `programs/system/src/system_instruction.rs` -> `verify_nonce_ok`
- Entrypoint: a builtin-program instruction in a transaction from an unprivileged fee-payer
- Attacker controls: a VoteInstruction referencing a vote account it does not own
- Exploit idea: Update vote-account authority, commission or state without the vote authority's signature.
- Invariant to test: vote-account mutations require the correct authorized signer.
- Expected Immunefi impact: Loss of Funds / Consensus — Critical
- Fast validation: write a program-test submitting the builtin/precompile instruction and assert the authorization/accounting invariant holds.
