# Q3128: crypto resolver falls back to globals in entropy.ts

## Question
resolveCrypto defaults digest and randomUUID to globalThis.crypto; can an attacker in the page substitute those globals so getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) produces predictable PKCE verifiers or idempotency keys?

## Target
- File/function: [src/utils/entropy.ts](src/utils/entropy.ts) - getEntropyDetailsFromUser (imported ? account : first eth ?? first solana), getEntropyDetailsFromAccount (address as entropyId + <chain>-address-verifier)
- Entrypoint: provider construction for any embedded wallet
- Attacker controls: which linked account is passed as signingAccount, wallet_index ordering, imported flag
- Exploit idea: Replace globalThis.crypto before constructing the client and observe generated values.
- Invariant to test: Security-critical randomness must not be taken from a mutable global at call time.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: stub globalThis.crypto with a deterministic implementation and assert getEntropyDetailsFromUser (imported ? account : first eth ?? first solana) detects or refuses it.
