# Q3078: multiple embedded wallets confuse the app in shouldCreateEmbeddedSolWallet.ts

## Question
shouldCreateEmbeddedSolWallet(user exposes lists that callers index into; can an attacker add a wallet so index-based access in the app selects a different wallet than before?

## Target
- File/function: [src/utils/shouldCreateEmbeddedSolWallet.ts](src/utils/shouldCreateEmbeddedSolWallet.ts) - shouldCreateEmbeddedSolWallet(user, createOnLogin)
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: linked solana accounts and the createOnLogin setting
- Exploit idea: Add a wallet and compare index-based selections before and after.
- Invariant to test: Wallet references must be stable identifiers, not list indices.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert shouldCreateEmbeddedSolWallet(user exposes stable identifiers for each wallet.
