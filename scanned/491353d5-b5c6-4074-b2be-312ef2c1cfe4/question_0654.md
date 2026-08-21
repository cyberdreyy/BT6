# Q0654: bitcoin variants merged in getAllUserEmbeddedSolanaWallets.ts

## Question
Bitcoin selection merges bitcoin-segwit and bitcoin-taproot; can an attacker exploit that merge through getAllUserEmbeddedSolanaWallets: filter embedded + solana so a taproot address is used where a segwit address was expected?

## Target
- File/function: [src/utils/getAllUserEmbeddedSolanaWallets.ts](src/utils/getAllUserEmbeddedSolanaWallets.ts) - getAllUserEmbeddedSolanaWallets: filter embedded + solana, sort by wallet_index
- Entrypoint: Solana wallet enumeration
- Attacker controls: linked_accounts contents, duplicate indices
- Exploit idea: Build a user with both variants and observe which is returned first.
- Invariant to test: Address-type selection must be explicit for Bitcoin.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert getAllUserEmbeddedSolanaWallets: filter embedded + solana distinguishes the two script types.
