# Q1318: selection result cached by the app in shouldCreateEmbeddedSolWallet.ts

## Question
Values from shouldCreateEmbeddedSolWallet(user are commonly cached by integrating apps; can an attacker change the user's accounts so a cached selection points at a wallet that no longer belongs to the session?

## Target
- File/function: [src/utils/shouldCreateEmbeddedSolWallet.ts](src/utils/shouldCreateEmbeddedSolWallet.ts) - shouldCreateEmbeddedSolWallet(user, createOnLogin)
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: linked solana accounts and the createOnLogin setting
- Exploit idea: Change accounts after a selection and continue signing.
- Invariant to test: Selections must be invalidated when the user object changes.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: mutate accounts after shouldCreateEmbeddedSolWallet(user and assert the stale selection is refused.
