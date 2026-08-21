# Q0658: bitcoin variants merged in shouldCreateEmbeddedSolWallet.ts

## Question
Bitcoin selection merges bitcoin-segwit and bitcoin-taproot; can an attacker exploit that merge through shouldCreateEmbeddedSolWallet(user so a taproot address is used where a segwit address was expected?

## Target
- File/function: [src/utils/shouldCreateEmbeddedSolWallet.ts](src/utils/shouldCreateEmbeddedSolWallet.ts) - shouldCreateEmbeddedSolWallet(user, createOnLogin)
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: linked solana accounts and the createOnLogin setting
- Exploit idea: Build a user with both variants and observe which is returned first.
- Invariant to test: Address-type selection must be explicit for Bitcoin.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert shouldCreateEmbeddedSolWallet(user distinguishes the two script types.
