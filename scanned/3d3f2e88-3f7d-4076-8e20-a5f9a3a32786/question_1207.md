# Q1207: off is the default when unset in shouldCreateEmbeddedEthWallet.ts

## Question
shouldCreateEmbeddedEthWallet(user defaults createOnLogin to 'off' when the option is absent; can an attacker exploit an app that assumes provisioning happened so subsequent code uses an undefined wallet?

## Target
- File/function: [src/utils/shouldCreateEmbeddedEthWallet.ts](src/utils/shouldCreateEmbeddedEthWallet.ts) - shouldCreateEmbeddedEthWallet(user, createOnLogin: 'off'|'users-without-wallets'|'all-users')
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: external wallets linked to the account and the createOnLogin setting
- Exploit idea: Log in with the option omitted and inspect downstream wallet usage.
- Invariant to test: Absent configuration must not silently disable a security-relevant provisioning step.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: omit the option and assert shouldCreateEmbeddedEthWallet(user reports the decision explicitly.
