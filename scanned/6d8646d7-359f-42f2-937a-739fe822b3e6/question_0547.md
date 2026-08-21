# Q0547: chain type filter is a string compare in shouldCreateEmbeddedEthWallet.ts

## Question
shouldCreateEmbeddedEthWallet(user filters on chain_type equality; can an attacker supply an account with an unexpected chain_type casing or alias so it is included or excluded incorrectly?

## Target
- File/function: [src/utils/shouldCreateEmbeddedEthWallet.ts](src/utils/shouldCreateEmbeddedEthWallet.ts) - shouldCreateEmbeddedEthWallet(user, createOnLogin: 'off'|'users-without-wallets'|'all-users')
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: external wallets linked to the account and the createOnLogin setting
- Exploit idea: Pass chain_type variants such as 'Ethereum' or 'ethereum '.
- Invariant to test: Chain type matching must be canonical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: table-test chain_type variants through shouldCreateEmbeddedEthWallet(user.
