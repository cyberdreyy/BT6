# Q0826: transaction type allow-list excludes 3 but allows 4 in isVersionedTransaction.ts

## Question
The type validator accepts 0,1,2,4 only; can an attacker pick a type through isVersionedTransaction ('version' in tx) so a field set intended for another type is serialised into the signed payload?

## Target
- File/function: [src/solana/isVersionedTransaction.ts](src/solana/isVersionedTransaction.ts) - isVersionedTransaction ('version' in tx)
- Entrypoint: Solana provider transaction handling
- Attacker controls: any object shaped to satisfy or defeat the 'version' in tx check
- Exploit idea: Send type 4 with EIP-4844 style fields, or omit fields required by the chosen type.
- Invariant to test: Type and field-set consistency must be enforced before signing.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: send inconsistent type/field combinations through isVersionedTransaction ('version' in tx) and assert rejection.
