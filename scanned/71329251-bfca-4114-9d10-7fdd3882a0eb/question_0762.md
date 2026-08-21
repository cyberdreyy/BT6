# Q0762: smart wallet found by type only in getAllUserEmbeddedEthereumWallets.ts

## Question
getUserSmartWallet returns the first account of type smart_wallet; can an attacker link an additional smart wallet so getAllUserEmbeddedEthereumWallets: filter embedded + ethereum returns one the user did not intend to use?

## Target
- File/function: [src/utils/getAllUserEmbeddedEthereumWallets.ts](src/utils/getAllUserEmbeddedEthereumWallets.ts) - getAllUserEmbeddedEthereumWallets: filter embedded + ethereum, sort by wallet_index
- Entrypoint: delegation, session signers, wallet lists
- Attacker controls: linked_accounts contents, duplicate wallet_index values
- Exploit idea: Link two smart wallets and observe the selection.
- Invariant to test: Smart-wallet selection must be explicit when several exist.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build a user with two smart wallets and assert getAllUserEmbeddedEthereumWallets: filter embedded + ethereum requires disambiguation.
