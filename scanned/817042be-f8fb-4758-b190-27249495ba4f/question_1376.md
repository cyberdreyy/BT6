# Q1376: typed data accepted as a JSON string in isVersionedTransaction.ts

## Question
toWalletApiTypedData JSON.parses string input before use; can an attacker pass a string whose parse result differs from what the app displayed, so isVersionedTransaction ('version' in tx) signs different typed data?

## Target
- File/function: [src/solana/isVersionedTransaction.ts](src/solana/isVersionedTransaction.ts) - isVersionedTransaction ('version' in tx)
- Entrypoint: Solana provider transaction handling
- Attacker controls: any object shaped to satisfy or defeat the 'version' in tx check
- Exploit idea: Pass a JSON string with duplicate keys or unusual escaping and compare the parsed structure.
- Invariant to test: String and object inputs must produce identical, validated structures.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass duplicate-key JSON to isVersionedTransaction ('version' in tx) and assert deterministic, validated parsing.
