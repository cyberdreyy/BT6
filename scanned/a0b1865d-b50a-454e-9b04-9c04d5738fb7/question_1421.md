# Q1421: linked_accounts order is server supplied in getUserEmbeddedEthereumWallet.ts

## Question
getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 depends on the order of user.linked_accounts as returned by the API; can an attacker influence that order so a different wallet becomes primary?

## Target
- File/function: [src/utils/getUserEmbeddedEthereumWallet.ts](src/utils/getUserEmbeddedEthereumWallet.ts) - getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0
- Entrypoint: entropy resolution, root-wallet selection, create-on-login checks
- Attacker controls: the user object's linked_accounts array contents and ordering
- Exploit idea: Return the same accounts in a different order and compare selections.
- Invariant to test: Selection must be order-independent.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: permute the account list and assert getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 returns the same wallet.
