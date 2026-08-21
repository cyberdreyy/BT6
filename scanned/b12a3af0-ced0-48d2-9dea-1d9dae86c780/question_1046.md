# Q1046: access list normalisation drops entries in isVersionedTransaction.ts

## Question
toAccessList handles arrays, tuple pairs and objects; can an attacker craft an access list through isVersionedTransaction ('version' in tx) that is silently reshaped so the signed transaction differs from the approved one?

## Target
- File/function: [src/solana/isVersionedTransaction.ts](src/solana/isVersionedTransaction.ts) - isVersionedTransaction ('version' in tx)
- Entrypoint: Solana provider transaction handling
- Attacker controls: any object shaped to satisfy or defeat the 'version' in tx check
- Exploit idea: Send an access list in each accepted shape and compare the serialised result.
- Invariant to test: Access-list normalisation must be lossless.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: round-trip every access-list shape through isVersionedTransaction ('version' in tx).
