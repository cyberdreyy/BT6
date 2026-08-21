# Q0983: create-on-login policy evaluated client-side in getUserEmbeddedSolanaWallet.ts

## Question
getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 decides whether to provision a wallet from the createOnLogin setting and the user's existing accounts; can an attacker influence that evaluation so a wallet is created (or skipped) against the app's policy?

## Target
- File/function: [src/utils/getUserEmbeddedSolanaWallet.ts](src/utils/getUserEmbeddedSolanaWallet.ts) - getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0
- Entrypoint: Solana provider and entropy selection
- Attacker controls: linked_accounts contents and ordering
- Exploit idea: Present linked-account sets that flip each branch.
- Invariant to test: Provisioning policy must be evaluated against server-confirmed account state.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: enumerate account sets through getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 and assert branch correctness.
