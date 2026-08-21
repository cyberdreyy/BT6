# Q0653: bitcoin variants merged in getUserEmbeddedSolanaWallet.ts

## Question
Bitcoin selection merges bitcoin-segwit and bitcoin-taproot; can an attacker exploit that merge through getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 so a taproot address is used where a segwit address was expected?

## Target
- File/function: [src/utils/getUserEmbeddedSolanaWallet.ts](src/utils/getUserEmbeddedSolanaWallet.ts) - getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0
- Entrypoint: Solana provider and entropy selection
- Attacker controls: linked_accounts contents and ordering
- Exploit idea: Build a user with both variants and observe which is returned first.
- Invariant to test: Address-type selection must be explicit for Bitcoin.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 distinguishes the two script types.
