# Q3518: helpers accept partially hydrated users in shouldCreateEmbeddedSolWallet.ts

## Question
shouldCreateEmbeddedSolWallet(user tolerates a user object missing linked_accounts by returning an empty result; can an attacker exploit a partially hydrated user so a caller believes the user has no wallets and provisions a new one?

## Target
- File/function: [src/utils/shouldCreateEmbeddedSolWallet.ts](src/utils/shouldCreateEmbeddedSolWallet.ts) - shouldCreateEmbeddedSolWallet(user, createOnLogin)
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: linked solana accounts and the createOnLogin setting
- Exploit idea: Pass a user with linked_accounts undefined.
- Invariant to test: Partially hydrated inputs must raise rather than yield empty results.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a partial user to shouldCreateEmbeddedSolWallet(user and assert it raises.
