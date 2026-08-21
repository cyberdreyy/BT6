# Q2256: versioned detection by a property name in isVersionedTransaction.ts

## Question
isVersionedTransaction only checks for a 'version' property; can an attacker pass an object carrying that property so isVersionedTransaction ('version' in tx) takes the versioned branch on a legacy transaction and serialises the wrong bytes?

## Target
- File/function: [src/solana/isVersionedTransaction.ts](src/solana/isVersionedTransaction.ts) - isVersionedTransaction ('version' in tx)
- Entrypoint: Solana provider transaction handling
- Attacker controls: any object shaped to satisfy or defeat the 'version' in tx check
- Exploit idea: Pass a legacy transaction object with an added version field.
- Invariant to test: Transaction kind detection must use structural validation.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass a spoofed object to isVersionedTransaction ('version' in tx) and assert detection is structural.
