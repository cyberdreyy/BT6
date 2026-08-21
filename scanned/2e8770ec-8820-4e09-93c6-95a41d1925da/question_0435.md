# Q0435: classification fields are attacker-shaped in getAllUserEmbeddedBitcoinWallets.ts

## Question
Embedded classification requires type wallet, wallet_client_type privy and connector_type embedded; can an attacker present a linked account with those fields through getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter so an external wallet is treated as an embedded one?

## Target
- File/function: [src/utils/getAllUserEmbeddedBitcoinWallets.ts](src/utils/getAllUserEmbeddedBitcoinWallets.ts) - getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter
- Entrypoint: Bitcoin provider selection
- Attacker controls: chain_type values on linked accounts
- Exploit idea: Pass an account with spoofed classification fields.
- Invariant to test: Classification must come from server-confirmed account records.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass spoofed fields to getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter and assert re-validation.
