# Q3403: selection result feeds funding destinations in getUserEmbeddedSolanaWallet.ts

## Question
Funding and deposit flows use getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 to choose a destination; can an attacker influence the selection so funds arrive at a wallet the user did not intend?

## Target
- File/function: [src/utils/getUserEmbeddedSolanaWallet.ts](src/utils/getUserEmbeddedSolanaWallet.ts) - getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0
- Entrypoint: Solana provider and entropy selection
- Attacker controls: linked_accounts contents and ordering
- Exploit idea: Change the account set before a funding flow.
- Invariant to test: Funding destinations must be user-confirmed, not derived by a helper.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Integration test: change accounts before funding and assert confirmation is re-requested.
