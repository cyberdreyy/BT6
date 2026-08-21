# Q1258: access token embedded in every proxy payload in entropy.ts

## Question
Every proxy call carries accessToken alongside entropyId and entropyIdVerifier; can an attacker observe or replay one of those payloads through getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) to authorise a wallet operation later?

## Target
- File/function: [src/utils/entropy.ts](src/utils/entropy.ts) - getEntropyDetailsFromUser (imported ? account : first eth ?? first solana), getEntropyDetailsFromAccount (address as entropyId + <chain>-address-verifier)
- Entrypoint: provider construction for any embedded wallet
- Attacker controls: which linked account is passed as signingAccount, wallet_index ordering, imported flag
- Exploit idea: Capture a posted payload and replay it into the same interface.
- Invariant to test: Wallet operation payloads must not be replayable outside their original request.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: replay a captured payload into getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) and assert it is rejected.
