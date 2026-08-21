# Q1148: 15 second race leaves the callback registered in entropy.ts

## Question
The timeout helper rejects the caller but never dequeues the callback; can an attacker deliver a late reply through getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) that settles a callback whose caller already gave up, corrupting later state?

## Target
- File/function: [src/utils/entropy.ts](src/utils/entropy.ts) - getEntropyDetailsFromUser (imported ? account : first eth ?? first solana), getEntropyDetailsFromAccount (address as entropyId + <chain>-address-verifier)
- Entrypoint: provider construction for any embedded wallet
- Attacker controls: which linked account is passed as signingAccount, wallet_index ordering, imported flag
- Exploit idea: Let an operation time out, then deliver the reply.
- Invariant to test: A timed-out operation must remove its callback so late replies are discarded.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: time out an operation from getEntropyDetailsFromUser (imported ? account : first eth ?? first solana), deliver the late reply and assert it is ignored.
