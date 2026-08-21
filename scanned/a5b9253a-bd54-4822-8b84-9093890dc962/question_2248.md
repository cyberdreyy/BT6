# Q2248: remove clears every signer in entropy.ts

## Question
removeSessionSigners writes additional_signers: [] or revokes all delegations; can an attacker use getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) to clear another party's legitimate signer while keeping their own access?

## Target
- File/function: [src/utils/entropy.ts](src/utils/entropy.ts) - getEntropyDetailsFromUser (imported ? account : first eth ?? first solana), getEntropyDetailsFromAccount (address as entropyId + <chain>-address-verifier)
- Entrypoint: provider construction for any embedded wallet
- Attacker controls: which linked account is passed as signingAccount, wallet_index ordering, imported flag
- Exploit idea: Call the remove path while multiple signers exist.
- Invariant to test: Signer removal must be scoped to the signer the user selected.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) with multiple signers present and assert only the requested one is removed.
