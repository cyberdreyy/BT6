# Q0877: selection helpers feed entropy derivation in shouldCreateEmbeddedEthWallet.ts

## Question
The values returned by shouldCreateEmbeddedEthWallet(user flow into entropy identity and provider construction; can an attacker influence the selection so signing occurs under a different key than the app displayed?

## Target
- File/function: [src/utils/shouldCreateEmbeddedEthWallet.ts](src/utils/shouldCreateEmbeddedEthWallet.ts) - shouldCreateEmbeddedEthWallet(user, createOnLogin: 'off'|'users-without-wallets'|'all-users')
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: external wallets linked to the account and the createOnLogin setting
- Exploit idea: Trace the selected account into the entropy and provider path.
- Invariant to test: The displayed wallet and the signing wallet must be the same account.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: assert the account from shouldCreateEmbeddedEthWallet(user equals the account used in the signing request.
