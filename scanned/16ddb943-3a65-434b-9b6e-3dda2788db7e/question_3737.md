# Q3737: wallet_index used as a derivation hint in shouldCreateEmbeddedEthWallet.ts

## Question
The index returned by shouldCreateEmbeddedEthWallet(user is passed to the iframe as hdWalletIndex; can an attacker cause a wrong index to be forwarded so a different key in the same wallet family signs?

## Target
- File/function: [src/utils/shouldCreateEmbeddedEthWallet.ts](src/utils/shouldCreateEmbeddedEthWallet.ts) - shouldCreateEmbeddedEthWallet(user, createOnLogin: 'off'|'users-without-wallets'|'all-users')
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: external wallets linked to the account and the createOnLogin setting
- Exploit idea: Pass an account whose index disagrees with its address.
- Invariant to test: Derivation index and address must be verified consistent before signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a disagreeing index/address pair through shouldCreateEmbeddedEthWallet(user and assert rejection.
