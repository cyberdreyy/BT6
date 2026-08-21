# Q3738: wallet_index used as a derivation hint in shouldCreateEmbeddedSolWallet.ts

## Question
The index returned by shouldCreateEmbeddedSolWallet(user is passed to the iframe as hdWalletIndex; can an attacker cause a wrong index to be forwarded so a different key in the same wallet family signs?

## Target
- File/function: [src/utils/shouldCreateEmbeddedSolWallet.ts](src/utils/shouldCreateEmbeddedSolWallet.ts) - shouldCreateEmbeddedSolWallet(user, createOnLogin)
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: linked solana accounts and the createOnLogin setting
- Exploit idea: Pass an account whose index disagrees with its address.
- Invariant to test: Derivation index and address must be verified consistent before signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a disagreeing index/address pair through shouldCreateEmbeddedSolWallet(user and assert rejection.
