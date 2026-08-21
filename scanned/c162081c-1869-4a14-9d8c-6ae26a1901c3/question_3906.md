# Q3906: tempo path selected by a predicate on the request in isVersionedTransaction.ts

## Question
The provider routes to the Tempo serializer when isTempoTransactionRequest matches; can an attacker shape a request so isVersionedTransaction ('version' in tx) takes the Tempo path on a non-Tempo chain, or the standard path for a Tempo transaction?

## Target
- File/function: [src/solana/isVersionedTransaction.ts](src/solana/isVersionedTransaction.ts) - isVersionedTransaction ('version' in tx)
- Entrypoint: Solana provider transaction handling
- Attacker controls: any object shaped to satisfy or defeat the 'version' in tx check
- Exploit idea: Submit hybrid field sets and compare the serialised output to the target chain.
- Invariant to test: Serializer selection must agree with the target chain and be rejected otherwise.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: submit hybrid requests to isVersionedTransaction ('version' in tx) and assert consistent routing.
