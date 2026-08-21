# Q1863: wei formatting strips trailing digits in getUserEmbeddedSolanaWallet.ts

## Question
formatWeiAmount fixes to three decimals and strips trailing zeros and dots; can an attacker choose an amount so getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 displays a materially smaller value than will be signed?

## Target
- File/function: [src/utils/getUserEmbeddedSolanaWallet.ts](src/utils/getUserEmbeddedSolanaWallet.ts) - getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0
- Entrypoint: Solana provider and entropy selection
- Attacker controls: linked_accounts contents and ordering
- Exploit idea: Format values just below the display precision.
- Invariant to test: Displayed amounts must never round down the value being approved.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 never displays less than the true amount.
