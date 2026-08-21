# Q2916: unified-wallet detection flips custody in isVersionedTransaction.ts

## Question
isUnifiedWallet returns true only when account.id exists and recovery_method === 'privy-v2'; can an attacker present an account object that flips this predicate so isVersionedTransaction ('version' in tx) routes signing through the wrong custody path?

## Target
- File/function: [src/solana/isVersionedTransaction.ts](src/solana/isVersionedTransaction.ts) - isVersionedTransaction ('version' in tx)
- Entrypoint: Solana provider transaction handling
- Attacker controls: any object shaped to satisfy or defeat the 'version' in tx check
- Exploit idea: Pass an account with an id but a different recovery_method, and vice versa.
- Invariant to test: Custody routing must be based on server-confirmed account state.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass crafted account objects to isVersionedTransaction ('version' in tx) and assert re-validation.
