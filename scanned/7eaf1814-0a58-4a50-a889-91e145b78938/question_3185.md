# Q3185: imported wallets mixed into the list in getAllUserEmbeddedBitcoinWallets.ts

## Question
Imported wallets appear alongside derived ones in getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter; can an attacker rely on that mixing so an imported wallet is used where a derived one was assumed (or vice versa) for entropy or recovery?

## Target
- File/function: [src/utils/getAllUserEmbeddedBitcoinWallets.ts](src/utils/getAllUserEmbeddedBitcoinWallets.ts) - getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter
- Entrypoint: Bitcoin provider selection
- Attacker controls: chain_type values on linked accounts
- Exploit idea: Include an imported wallet and follow the entropy path.
- Invariant to test: Imported and derived wallets must be distinguished wherever custody differs.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter marks imported wallets distinctly.
