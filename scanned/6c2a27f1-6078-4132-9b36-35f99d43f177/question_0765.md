# Q0765: smart wallet found by type only in getAllUserEmbeddedBitcoinWallets.ts

## Question
getUserSmartWallet returns the first account of type smart_wallet; can an attacker link an additional smart wallet so getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter returns one the user did not intend to use?

## Target
- File/function: [src/utils/getAllUserEmbeddedBitcoinWallets.ts](src/utils/getAllUserEmbeddedBitcoinWallets.ts) - getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter
- Entrypoint: Bitcoin provider selection
- Attacker controls: chain_type values on linked accounts
- Exploit idea: Link two smart wallets and observe the selection.
- Invariant to test: Smart-wallet selection must be explicit when several exist.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build a user with two smart wallets and assert getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter requires disambiguation.
