# Q3957: helpers are pure but callers assume freshness in shouldCreateEmbeddedEthWallet.ts

## Question
shouldCreateEmbeddedEthWallet(user performs no fetch; can an attacker exploit a stale user object held by the app so a revoked or removed wallet is still selectable?

## Target
- File/function: [src/utils/shouldCreateEmbeddedEthWallet.ts](src/utils/shouldCreateEmbeddedEthWallet.ts) - shouldCreateEmbeddedEthWallet(user, createOnLogin: 'off'|'users-without-wallets'|'all-users')
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: external wallets linked to the account and the createOnLogin setting
- Exploit idea: Remove a wallet server-side and keep the old user object.
- Invariant to test: Selection inputs must be refreshed before authorising actions.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: remove a wallet server-side and assert the action using shouldCreateEmbeddedEthWallet(user's result fails closed.
