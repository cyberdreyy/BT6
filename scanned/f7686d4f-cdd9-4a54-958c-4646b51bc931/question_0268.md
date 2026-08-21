# Q0268: singleton queue shared across Privy clients in entropy.ts

## Question
The callback queue is a module-level singleton shared by every proxy instance; can an attacker in a multi-client or multi-user page make one client's reply settle another client's pending request via getEntropyDetailsFromUser (imported ? account : first eth ?? first solana)?

## Target
- File/function: [src/utils/entropy.ts](src/utils/entropy.ts) - getEntropyDetailsFromUser (imported ? account : first eth ?? first solana), getEntropyDetailsFromAccount (address as entropyId + <chain>-address-verifier)
- Entrypoint: provider construction for any embedded wallet
- Attacker controls: which linked account is passed as signingAccount, wallet_index ordering, imported flag
- Exploit idea: Instantiate two clients, start an operation on each, and deliver one reply.
- Invariant to test: Callback state must be scoped per client instance.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: create two proxies, enqueue on both through getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) and assert their callback maps are disjoint.
