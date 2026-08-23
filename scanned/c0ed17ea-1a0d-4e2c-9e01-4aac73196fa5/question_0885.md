# Q885: system_instruction::nonce_inx_independent_nonce_authority_ok — system allocate/assign abuse

## Question
Can an unprivileged attacker, through a builtin-program instruction in a transaction from an unprivileged fee-payer, reach `system_instruction::nonce_inx_independent_nonce_authority_ok` and use CreateAccount/Allocate/Assign so an account is created over or reassigned away from another user's account, so that the invariant "account creation never overwrites an existing funded account's owner/data" is violated, leading to Loss of Funds?

## Target
- File/function: `programs/system/src/system_instruction.rs` -> `nonce_inx_independent_nonce_authority_ok`
- Entrypoint: a builtin-program instruction in a transaction from an unprivileged fee-payer
- Attacker controls: the target address, space and owner in a System instruction
- Exploit idea: Use CreateAccount/Allocate/Assign so an account is created over or reassigned away from another user's account.
- Invariant to test: account creation never overwrites an existing funded account's owner/data.
- Expected Immunefi impact: Loss of Funds — Critical
- Fast validation: write a program-test submitting the builtin/precompile instruction and assert the authorization/accounting invariant holds.
