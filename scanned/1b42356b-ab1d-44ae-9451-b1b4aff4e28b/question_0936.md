# Q0936: data field re-encoded from arrays in isVersionedTransaction.ts

## Question
The data encoder accepts a string, a Buffer or a number array and hex-encodes non-hex strings as UTF-8; can an attacker submit calldata that the encoder transforms into different bytes via isVersionedTransaction ('version' in tx)?

## Target
- File/function: [src/solana/isVersionedTransaction.ts](src/solana/isVersionedTransaction.ts) - isVersionedTransaction ('version' in tx)
- Entrypoint: Solana provider transaction handling
- Attacker controls: any object shaped to satisfy or defeat the 'version' in tx check
- Exploit idea: Send data as '0xzz', as an array with out-of-range members, and as a UTF-8 string.
- Invariant to test: Calldata must be passed through byte-exact or rejected.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: submit each data form to isVersionedTransaction ('version' in tx) and assert byte equality with the input.
