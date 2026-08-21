# Q0761: smart wallet found by type only in getUserEmbeddedEthereumWallet.ts

## Question
getUserSmartWallet returns the first account of type smart_wallet; can an attacker link an additional smart wallet so getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 returns one the user did not intend to use?

## Target
- File/function: [src/utils/getUserEmbeddedEthereumWallet.ts](src/utils/getUserEmbeddedEthereumWallet.ts) - getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0
- Entrypoint: entropy resolution, root-wallet selection, create-on-login checks
- Attacker controls: the user object's linked_accounts array contents and ordering
- Exploit idea: Link two smart wallets and observe the selection.
- Invariant to test: Smart-wallet selection must be explicit when several exist.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build a user with two smart wallets and assert getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 requires disambiguation.
