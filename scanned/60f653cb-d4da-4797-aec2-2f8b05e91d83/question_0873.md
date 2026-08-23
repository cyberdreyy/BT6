# Q873: system_instruction::authorize_nonce_account — vote credit inflation

## Question
Can an unprivileged attacker, through a builtin-program instruction in a transaction from an unprivileged fee-payer, reach `system_instruction::authorize_nonce_account` and craft a vote so recorded credits/lockouts diverge from the true tower, affecting rewards or consensus counting, so that the invariant "vote credits/lockouts reflect only validly submitted votes" is violated, leading to Consensus/Safety Violation?

## Target
- File/function: `programs/system/src/system_instruction.rs` -> `authorize_nonce_account`
- Entrypoint: a builtin-program instruction in a transaction from an unprivileged fee-payer
- Attacker controls: the slots/hash in a Vote/VoteStateUpdate it can submit for its own vote account
- Exploit idea: Craft a vote so recorded credits/lockouts diverge from the true tower, affecting rewards or consensus counting.
- Invariant to test: vote credits/lockouts reflect only validly submitted votes.
- Expected Immunefi impact: Consensus/Safety Violation — Critical
- Fast validation: write a program-test submitting the builtin/precompile instruction and assert the authorization/accounting invariant holds.
