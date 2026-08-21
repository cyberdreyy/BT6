# Q3295: solana and ethereum lists share the predicate in getAllUserEmbeddedBitcoinWallets.ts

## Question
Both list helpers use the same embedded predicate with a chain filter; can an attacker produce an account whose chain_type is absent so it is excluded from both lists yet still signable?

## Target
- File/function: [src/utils/getAllUserEmbeddedBitcoinWallets.ts](src/utils/getAllUserEmbeddedBitcoinWallets.ts) - getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter
- Entrypoint: Bitcoin provider selection
- Attacker controls: chain_type values on linked accounts
- Exploit idea: Omit chain_type on an embedded account.
- Invariant to test: Every signable account must appear in exactly one enumeration.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: omit chain_type and assert getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter surfaces the account or rejects it.
