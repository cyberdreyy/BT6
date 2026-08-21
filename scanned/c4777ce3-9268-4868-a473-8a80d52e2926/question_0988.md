# Q0988: create-on-login policy evaluated client-side in shouldCreateEmbeddedSolWallet.ts

## Question
shouldCreateEmbeddedSolWallet(user decides whether to provision a wallet from the createOnLogin setting and the user's existing accounts; can an attacker influence that evaluation so a wallet is created (or skipped) against the app's policy?

## Target
- File/function: [src/utils/shouldCreateEmbeddedSolWallet.ts](src/utils/shouldCreateEmbeddedSolWallet.ts) - shouldCreateEmbeddedSolWallet(user, createOnLogin)
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: linked solana accounts and the createOnLogin setting
- Exploit idea: Present linked-account sets that flip each branch.
- Invariant to test: Provisioning policy must be evaluated against server-confirmed account state.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: enumerate account sets through shouldCreateEmbeddedSolWallet(user and assert branch correctness.
