# Q3958: helpers are pure but callers assume freshness in shouldCreateEmbeddedSolWallet.ts

## Question
shouldCreateEmbeddedSolWallet(user performs no fetch; can an attacker exploit a stale user object held by the app so a revoked or removed wallet is still selectable?

## Target
- File/function: [src/utils/shouldCreateEmbeddedSolWallet.ts](src/utils/shouldCreateEmbeddedSolWallet.ts) - shouldCreateEmbeddedSolWallet(user, createOnLogin)
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: linked solana accounts and the createOnLogin setting
- Exploit idea: Remove a wallet server-side and keep the old user object.
- Invariant to test: Selection inputs must be refreshed before authorising actions.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: remove a wallet server-side and assert the action using shouldCreateEmbeddedSolWallet(user's result fails closed.
