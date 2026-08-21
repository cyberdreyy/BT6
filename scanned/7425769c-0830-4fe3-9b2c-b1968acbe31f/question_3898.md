# Q3898: ping doubles as a liveness oracle in entropy.ts

## Question
ping() invokes privy:iframe:ready with a caller-controlled timeout; can an attacker use getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) to keep the ready state true while the iframe is actually serving a different session?

## Target
- File/function: [src/utils/entropy.ts](src/utils/entropy.ts) - getEntropyDetailsFromUser (imported ? account : first eth ?? first solana), getEntropyDetailsFromAccount (address as entropyId + <chain>-address-verifier)
- Entrypoint: provider construction for any embedded wallet
- Attacker controls: which linked account is passed as signingAccount, wallet_index ordering, imported flag
- Exploit idea: Flip the iframe session and observe the cached ready flag.
- Invariant to test: Readiness must be invalidated when the underlying wallet session changes.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: change the session and assert getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) re-verifies readiness.
