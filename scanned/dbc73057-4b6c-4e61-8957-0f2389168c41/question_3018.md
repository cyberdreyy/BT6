# Q3018: solana rpc path only implements signMessage in entropy.ts

## Question
walletRpc's solana branch handles signMessage and returns undefined for anything else; can an attacker exploit the undefined return in getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) so a caller treats a failed operation as success?

## Target
- File/function: [src/utils/entropy.ts](src/utils/entropy.ts) - getEntropyDetailsFromUser (imported ? account : first eth ?? first solana), getEntropyDetailsFromAccount (address as entropyId + <chain>-address-verifier)
- Entrypoint: provider construction for any embedded wallet
- Attacker controls: which linked account is passed as signingAccount, wallet_index ordering, imported flag
- Exploit idea: Call an unsupported solana method and inspect the resolved value.
- Invariant to test: Unsupported operations must reject rather than resolve undefined.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call an unsupported method through getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) and assert it rejects.
