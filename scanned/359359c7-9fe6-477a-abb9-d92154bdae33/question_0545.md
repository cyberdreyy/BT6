# Q0545: chain type filter is a string compare in getAllUserEmbeddedBitcoinWallets.ts

## Question
getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter filters on chain_type equality; can an attacker supply an account with an unexpected chain_type casing or alias so it is included or excluded incorrectly?

## Target
- File/function: [src/utils/getAllUserEmbeddedBitcoinWallets.ts](src/utils/getAllUserEmbeddedBitcoinWallets.ts) - getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter
- Entrypoint: Bitcoin provider selection
- Attacker controls: chain_type values on linked accounts
- Exploit idea: Pass chain_type variants such as 'Ethereum' or 'ethereum '.
- Invariant to test: Chain type matching must be canonical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: table-test chain_type variants through getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter.
