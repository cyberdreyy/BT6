# Q0437: classification fields are attacker-shaped in shouldCreateEmbeddedEthWallet.ts

## Question
Embedded classification requires type wallet, wallet_client_type privy and connector_type embedded; can an attacker present a linked account with those fields through shouldCreateEmbeddedEthWallet(user so an external wallet is treated as an embedded one?

## Target
- File/function: [src/utils/shouldCreateEmbeddedEthWallet.ts](src/utils/shouldCreateEmbeddedEthWallet.ts) - shouldCreateEmbeddedEthWallet(user, createOnLogin: 'off'|'users-without-wallets'|'all-users')
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: external wallets linked to the account and the createOnLogin setting
- Exploit idea: Pass an account with spoofed classification fields.
- Invariant to test: Classification must come from server-confirmed account records.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass spoofed fields to shouldCreateEmbeddedEthWallet(user and assert re-validation.
