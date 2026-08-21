# Q1156: fee payer signature parity inference in isVersionedTransaction.ts

## Question
toFeePayerSignature derives yParity from v-27 when yParity is absent; can an attacker supply a v value that yields a wrong parity accepted by isVersionedTransaction ('version' in tx)?

## Target
- File/function: [src/solana/isVersionedTransaction.ts](src/solana/isVersionedTransaction.ts) - isVersionedTransaction ('version' in tx)
- Entrypoint: Solana provider transaction handling
- Attacker controls: any object shaped to satisfy or defeat the 'version' in tx check
- Exploit idea: Send v values such as 0, 1, 35 and 36 and inspect the derived parity.
- Invariant to test: Signature parity must be derived unambiguously or rejected.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: table-test v/yParity inputs through isVersionedTransaction ('version' in tx).
