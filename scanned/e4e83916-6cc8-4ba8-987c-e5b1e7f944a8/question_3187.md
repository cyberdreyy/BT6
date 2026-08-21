# Q3187: imported wallets mixed into the list in shouldCreateEmbeddedEthWallet.ts

## Question
Imported wallets appear alongside derived ones in shouldCreateEmbeddedEthWallet(user; can an attacker rely on that mixing so an imported wallet is used where a derived one was assumed (or vice versa) for entropy or recovery?

## Target
- File/function: [src/utils/shouldCreateEmbeddedEthWallet.ts](src/utils/shouldCreateEmbeddedEthWallet.ts) - shouldCreateEmbeddedEthWallet(user, createOnLogin: 'off'|'users-without-wallets'|'all-users')
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: external wallets linked to the account and the createOnLogin setting
- Exploit idea: Include an imported wallet and follow the entropy path.
- Invariant to test: Imported and derived wallets must be distinguished wherever custody differs.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert shouldCreateEmbeddedEthWallet(user marks imported wallets distinctly.
