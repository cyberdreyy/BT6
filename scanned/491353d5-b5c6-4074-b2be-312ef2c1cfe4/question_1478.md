# Q1478: entropyId is just the wallet address in entropy.ts

## Question
getEntropyDetailsFromAccount uses the account address as the entropyId; can an attacker pass an address they merely know through getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) and cause the iframe to load or recover the wrong wallet?

## Target
- File/function: [src/utils/entropy.ts](src/utils/entropy.ts) - getEntropyDetailsFromUser (imported ? account : first eth ?? first solana), getEntropyDetailsFromAccount (address as entropyId + <chain>-address-verifier)
- Entrypoint: provider construction for any embedded wallet
- Attacker controls: which linked account is passed as signingAccount, wallet_index ordering, imported flag
- Exploit idea: Call the provider path with a foreign address as entropyId.
- Invariant to test: Entropy identifiers must be validated against the authenticated user's own accounts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign address into getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) and assert it is rejected before the proxy call.
