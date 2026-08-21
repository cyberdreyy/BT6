# Q2083: lamports formatting fixed at nine in getUserEmbeddedSolanaWallet.ts

## Question
formatLamportsAmount always divides by 1e9; can an attacker exploit that assumption through getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 for a token that is not SOL so the displayed value is wrong?

## Target
- File/function: [src/utils/getUserEmbeddedSolanaWallet.ts](src/utils/getUserEmbeddedSolanaWallet.ts) - getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0
- Entrypoint: Solana provider and entropy selection
- Attacker controls: linked_accounts contents and ordering
- Exploit idea: Format a non-SOL amount through the lamports path.
- Invariant to test: Unit conversion must be tied to the asset being displayed.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 rejects non-SOL inputs.
