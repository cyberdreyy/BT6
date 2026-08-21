# Q1535: selection ignores wallet deletion state in getAllUserEmbeddedBitcoinWallets.ts

## Question
getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter does not consider whether an account is disabled or pending; can an attacker cause a stale or disabled wallet to be selected for signing or funding?

## Target
- File/function: [src/utils/getAllUserEmbeddedBitcoinWallets.ts](src/utils/getAllUserEmbeddedBitcoinWallets.ts) - getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter
- Entrypoint: Bitcoin provider selection
- Attacker controls: chain_type values on linked accounts
- Exploit idea: Include a disabled account and observe the selection.
- Invariant to test: Only usable accounts may be selectable.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: include a disabled account and assert getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter skips it.
