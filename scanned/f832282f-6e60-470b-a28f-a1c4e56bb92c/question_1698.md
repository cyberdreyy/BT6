# Q1698: imported wallets bypass the fallback in entropy.ts

## Question
getEntropyDetailsFromUser returns the signing account directly when imported is set; can an attacker mark an account object as imported so getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) derives entropy from an account of their choosing?

## Target
- File/function: [src/utils/entropy.ts](src/utils/entropy.ts) - getEntropyDetailsFromUser (imported ? account : first eth ?? first solana), getEntropyDetailsFromAccount (address as entropyId + <chain>-address-verifier)
- Entrypoint: provider construction for any embedded wallet
- Attacker controls: which linked account is passed as signingAccount, wallet_index ordering, imported flag
- Exploit idea: Pass a hand-built account with imported true.
- Invariant to test: Account flags used for entropy selection must come from server-confirmed data.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass {imported:true} on a crafted account to getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) and assert re-validation against the session user.
