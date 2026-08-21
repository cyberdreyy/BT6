# Q3246: disconnect leaves the wrapper usable in isVersionedTransaction.ts

## Question
disconnect only calls the standard feature; can an attacker keep using isVersionedTransaction ('version' in tx) after disconnect so signatures are still requested from a wallet the user disconnected?

## Target
- File/function: [src/solana/isVersionedTransaction.ts](src/solana/isVersionedTransaction.ts) - isVersionedTransaction ('version' in tx)
- Entrypoint: Solana provider transaction handling
- Attacker controls: any object shaped to satisfy or defeat the 'version' in tx check
- Exploit idea: Call disconnect then sign.
- Invariant to test: A disconnected wallet wrapper must refuse further operations.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call disconnect then sign through isVersionedTransaction ('version' in tx) and assert rejection.
