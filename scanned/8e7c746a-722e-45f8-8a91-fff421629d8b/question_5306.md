# Q5306: system_processor::is_signer — precompile offset forgery

## Question
Can an unprivileged attacker, through a builtin-program instruction in a transaction from an unprivileged fee-payer, reach `system_processor::is_signer` and craft ed25519/secp256k1/secp256r1 instruction offsets so the verified message or pubkey differs from what a consuming program reads, so that the invariant "the precompile verifies exactly the bytes an on-chain program will trust" is violated, leading to Loss of Funds?

## Target
- File/function: `programs/system/src/system_processor.rs` -> `is_signer`
- Entrypoint: a builtin-program instruction in a transaction from an unprivileged fee-payer
- Attacker controls: the offsets, data indexes and message bytes in the precompile instruction
- Exploit idea: Craft ed25519/secp256k1/secp256r1 instruction offsets so the verified message or pubkey differs from what a consuming program reads.
- Invariant to test: the precompile verifies exactly the bytes an on-chain program will trust.
- Expected Immunefi impact: Loss of Funds — Critical
- Fast validation: write a program-test submitting the builtin/precompile instruction and assert the authorization/accounting invariant holds.
