# Q0598: error branch forges a wallet error in entropy.ts

## Question
handleEmbeddedWalletMessages routes any reply with an error field into reject(new PrivyIframeError(type, message)); can an attacker deliver an error reply with type 'wallet_not_on_device' so getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) starts a recovery flow?

## Target
- File/function: [src/utils/entropy.ts](src/utils/entropy.ts) - getEntropyDetailsFromUser (imported ? account : first eth ?? first solana), getEntropyDetailsFromAccount (address as entropyId + <chain>-address-verifier)
- Entrypoint: provider construction for any embedded wallet
- Attacker controls: which linked account is passed as signingAccount, wallet_index ordering, imported flag
- Exploit idea: Post an error reply with the recovery-triggering type for a pending connect.
- Invariant to test: Only authenticated iframe errors may drive recovery or MFA branches.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: deliver a forged error reply through getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) and assert no recovery is attempted.
