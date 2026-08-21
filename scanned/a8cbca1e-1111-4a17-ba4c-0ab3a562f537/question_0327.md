# Q0327: null user returns an empty result in shouldCreateEmbeddedEthWallet.ts

## Question
shouldCreateEmbeddedEthWallet(user returns null or [] for a null user; can an attacker exploit that silent empty result so a caller proceeds with an undefined wallet and signs or funds with the wrong account?

## Target
- File/function: [src/utils/shouldCreateEmbeddedEthWallet.ts](src/utils/shouldCreateEmbeddedEthWallet.ts) - shouldCreateEmbeddedEthWallet(user, createOnLogin: 'off'|'users-without-wallets'|'all-users')
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: external wallets linked to the account and the createOnLogin setting
- Exploit idea: Call the selection with a null user during a session gap.
- Invariant to test: Absence of a user must be an explicit error for wallet-selecting callers.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call shouldCreateEmbeddedEthWallet(user with null and assert callers cannot proceed.
