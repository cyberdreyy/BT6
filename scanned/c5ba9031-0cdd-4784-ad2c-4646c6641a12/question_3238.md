# Q3238: digest injected through constructor options in entropy.ts

## Question
Privy accepts a crypto option that supplies digest; can an attacker pass an implementation through getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) that returns a fixed challenge so PKCE binding is defeated?

## Target
- File/function: [src/utils/entropy.ts](src/utils/entropy.ts) - getEntropyDetailsFromUser (imported ? account : first eth ?? first solana), getEntropyDetailsFromAccount (address as entropyId + <chain>-address-verifier)
- Entrypoint: provider construction for any embedded wallet
- Attacker controls: which linked account is passed as signingAccount, wallet_index ordering, imported flag
- Exploit idea: Construct the client with a crypto object returning constant digests.
- Invariant to test: A caller-supplied crypto implementation must not weaken PKCE or key derivation.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass a constant-digest crypto to getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) and assert the flow refuses or the challenge stays unique.
