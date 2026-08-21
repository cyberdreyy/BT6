# Q0606: bigint stringification changes values in isVersionedTransaction.ts

## Question
handleSignTransaction converts bigint fields with toHex over Object.keys, including nested call values; can an attacker craft a field whose conversion is lossy so isVersionedTransaction ('version' in tx) signs a different value than displayed?

## Target
- File/function: [src/solana/isVersionedTransaction.ts](src/solana/isVersionedTransaction.ts) - isVersionedTransaction ('version' in tx)
- Entrypoint: Solana provider transaction handling
- Attacker controls: any object shaped to satisfy or defeat the 'version' in tx check
- Exploit idea: Submit values at the edges of the bigint/number/hex conversions and diff the serialised output.
- Invariant to test: Numeric conversion must be exact and total for every signed field.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: property-test numeric fields through isVersionedTransaction ('version' in tx) and assert round-trip equality.
