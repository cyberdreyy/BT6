# Q0432: classification fields are attacker-shaped in getAllUserEmbeddedEthereumWallets.ts

## Question
Embedded classification requires type wallet, wallet_client_type privy and connector_type embedded; can an attacker present a linked account with those fields through getAllUserEmbeddedEthereumWallets: filter embedded + ethereum so an external wallet is treated as an embedded one?

## Target
- File/function: [src/utils/getAllUserEmbeddedEthereumWallets.ts](src/utils/getAllUserEmbeddedEthereumWallets.ts) - getAllUserEmbeddedEthereumWallets: filter embedded + ethereum, sort by wallet_index
- Entrypoint: delegation, session signers, wallet lists
- Attacker controls: linked_accounts contents, duplicate wallet_index values
- Exploit idea: Pass an account with spoofed classification fields.
- Invariant to test: Classification must come from server-confirmed account records.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass spoofed fields to getAllUserEmbeddedEthereumWallets: filter embedded + ethereum and assert re-validation.
