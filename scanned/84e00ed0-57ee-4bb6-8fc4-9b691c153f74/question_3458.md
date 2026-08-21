# Q3458: wallet create returns before the user is refreshed in entropy.ts

## Question
create()/add() call refreshSession after the iframe returns; can an attacker interleave a session change through getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) so the created wallet is attributed to a different user object?

## Target
- File/function: [src/utils/entropy.ts](src/utils/entropy.ts) - getEntropyDetailsFromUser (imported ? account : first eth ?? first solana), getEntropyDetailsFromAccount (address as entropyId + <chain>-address-verifier)
- Entrypoint: provider construction for any embedded wallet
- Attacker controls: which linked account is passed as signingAccount, wallet_index ordering, imported flag
- Exploit idea: Change the active user between the iframe result and the refresh.
- Invariant to test: Wallet creation results must be attributed to the identity that requested them.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: switch users mid-call in getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) and assert the operation aborts.
