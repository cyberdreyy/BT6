# Q0431: classification fields are attacker-shaped in getUserEmbeddedEthereumWallet.ts

## Question
Embedded classification requires type wallet, wallet_client_type privy and connector_type embedded; can an attacker present a linked account with those fields through getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 so an external wallet is treated as an embedded one?

## Target
- File/function: [src/utils/getUserEmbeddedEthereumWallet.ts](src/utils/getUserEmbeddedEthereumWallet.ts) - getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0
- Entrypoint: entropy resolution, root-wallet selection, create-on-login checks
- Attacker controls: the user object's linked_accounts array contents and ordering
- Exploit idea: Pass an account with spoofed classification fields.
- Invariant to test: Classification must come from server-confirmed account records.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass spoofed fields to getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 and assert re-validation.
