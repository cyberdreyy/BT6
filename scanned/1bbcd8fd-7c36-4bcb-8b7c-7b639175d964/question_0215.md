# Q0215: sort is not stable across equal indices in getAllUserEmbeddedBitcoinWallets.ts

## Question
getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter sorts by wallet_index with a numeric comparator; can an attacker create equal indices so the resulting order (and therefore the selected wallet) varies between runs or engines?

## Target
- File/function: [src/utils/getAllUserEmbeddedBitcoinWallets.ts](src/utils/getAllUserEmbeddedBitcoinWallets.ts) - getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter
- Entrypoint: Bitcoin provider selection
- Attacker controls: chain_type values on linked accounts
- Exploit idea: Create two accounts with identical wallet_index and compare orderings.
- Invariant to test: Selection must be deterministic for any account set.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter is deterministic for equal-index accounts.
