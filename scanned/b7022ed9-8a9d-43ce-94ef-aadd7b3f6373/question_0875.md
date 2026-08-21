# Q0875: selection helpers feed entropy derivation in getAllUserEmbeddedBitcoinWallets.ts

## Question
The values returned by getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter flow into entropy identity and provider construction; can an attacker influence the selection so signing occurs under a different key than the app displayed?

## Target
- File/function: [src/utils/getAllUserEmbeddedBitcoinWallets.ts](src/utils/getAllUserEmbeddedBitcoinWallets.ts) - getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter
- Entrypoint: Bitcoin provider selection
- Attacker controls: chain_type values on linked accounts
- Exploit idea: Trace the selected account into the entropy and provider path.
- Invariant to test: The displayed wallet and the signing wallet must be the same account.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: assert the account from getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter equals the account used in the signing request.
