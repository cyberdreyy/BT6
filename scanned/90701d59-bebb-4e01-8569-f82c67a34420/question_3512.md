# Q3512: helpers accept partially hydrated users in getAllUserEmbeddedEthereumWallets.ts

## Question
getAllUserEmbeddedEthereumWallets: filter embedded + ethereum tolerates a user object missing linked_accounts by returning an empty result; can an attacker exploit a partially hydrated user so a caller believes the user has no wallets and provisions a new one?

## Target
- File/function: [src/utils/getAllUserEmbeddedEthereumWallets.ts](src/utils/getAllUserEmbeddedEthereumWallets.ts) - getAllUserEmbeddedEthereumWallets: filter embedded + ethereum, sort by wallet_index
- Entrypoint: delegation, session signers, wallet lists
- Attacker controls: linked_accounts contents, duplicate wallet_index values
- Exploit idea: Pass a user with linked_accounts undefined.
- Invariant to test: Partially hydrated inputs must raise rather than yield empty results.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a partial user to getAllUserEmbeddedEthereumWallets: filter embedded + ethereum and assert it raises.
