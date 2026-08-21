# Q3511: helpers accept partially hydrated users in getUserEmbeddedEthereumWallet.ts

## Question
getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 tolerates a user object missing linked_accounts by returning an empty result; can an attacker exploit a partially hydrated user so a caller believes the user has no wallets and provisions a new one?

## Target
- File/function: [src/utils/getUserEmbeddedEthereumWallet.ts](src/utils/getUserEmbeddedEthereumWallet.ts) - getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0
- Entrypoint: entropy resolution, root-wallet selection, create-on-login checks
- Attacker controls: the user object's linked_accounts array contents and ordering
- Exploit idea: Pass a user with linked_accounts undefined.
- Invariant to test: Partially hydrated inputs must raise rather than yield empty results.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a partial user to getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 and assert it raises.
