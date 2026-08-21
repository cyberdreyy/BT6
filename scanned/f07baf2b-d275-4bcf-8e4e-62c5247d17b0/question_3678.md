# Q3678: add() skips the access token check in server mode in entropy.ts

## Question
In user-controlled-server-wallets-only mode, add() creates through the wallet-api without the local access-token guard the other branch applies; can an attacker use getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) to add a wallet without a live session?

## Target
- File/function: [src/utils/entropy.ts](src/utils/entropy.ts) - getEntropyDetailsFromUser (imported ? account : first eth ?? first solana), getEntropyDetailsFromAccount (address as entropyId + <chain>-address-verifier)
- Entrypoint: provider construction for any embedded wallet
- Attacker controls: which linked account is passed as signingAccount, wallet_index ordering, imported flag
- Exploit idea: Set the config mode and call add with no token present.
- Invariant to test: Every wallet-creating branch must require an authenticated session.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: clear tokens, set server mode and assert getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) refuses.
