# Q0276: populate then sign is not atomic in isVersionedTransaction.ts

## Question
handleSendTransaction populates, then signs, then broadcasts; can an attacker mutate the transaction object between those steps so the user approves one payload and another is signed via isVersionedTransaction ('version' in tx)?

## Target
- File/function: [src/solana/isVersionedTransaction.ts](src/solana/isVersionedTransaction.ts) - isVersionedTransaction ('version' in tx)
- Entrypoint: Solana provider transaction handling
- Attacker controls: any object shaped to satisfy or defeat the 'version' in tx check
- Exploit idea: Pass an object with getters that change value between the populate and sign reads.
- Invariant to test: The signed payload must be a frozen snapshot of what was approved.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass a self-mutating object to isVersionedTransaction ('version' in tx) and assert the signed payload equals the approved snapshot.
