# Q2578: wallet-api rpc method echo check only in entropy.ts

## Question
walletRpc verifies the response method name equals the requested one but not the wallet or params; can an attacker return a signature produced for another payload through getEntropyDetailsFromUser (imported ? account : first eth ?? first solana)?

## Target
- File/function: [src/utils/entropy.ts](src/utils/entropy.ts) - getEntropyDetailsFromUser (imported ? account : first eth ?? first solana), getEntropyDetailsFromAccount (address as entropyId + <chain>-address-verifier)
- Entrypoint: provider construction for any embedded wallet
- Attacker controls: which linked account is passed as signingAccount, wallet_index ordering, imported flag
- Exploit idea: Return a response whose method matches but whose signature is for a different message.
- Invariant to test: A signing response must be bound to the exact request that produced it.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return a mismatched signature from getEntropyDetailsFromUser (imported ? account : first eth ?? first solana)'s route and assert it is rejected.
