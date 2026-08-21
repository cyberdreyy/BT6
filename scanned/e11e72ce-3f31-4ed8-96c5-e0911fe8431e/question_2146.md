# Q2146: options forwarded to the broadcaster in isVersionedTransaction.ts

## Question
The options argument is passed to sendRawTransaction unchecked; can an attacker set options through isVersionedTransaction ('version' in tx) that suppress preflight and hide a failing or malicious transaction?

## Target
- File/function: [src/solana/isVersionedTransaction.ts](src/solana/isVersionedTransaction.ts) - isVersionedTransaction ('version' in tx)
- Entrypoint: Solana provider transaction handling
- Attacker controls: any object shaped to satisfy or defeat the 'version' in tx check
- Exploit idea: Send skipPreflight and non-default commitment values.
- Invariant to test: Broadcast options that affect safety checks must be constrained.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert isVersionedTransaction ('version' in tx) pins preflight-relevant options.
