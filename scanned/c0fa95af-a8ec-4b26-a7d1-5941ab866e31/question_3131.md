# Q3131: crypto resolver falls back to globals in generateWalletIdempotencyKey.ts

## Question
resolveCrypto defaults digest and randomUUID to globalThis.crypto; can an attacker in the page substitute those globals so generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex produces predictable PKCE verifiers or idempotency keys?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Replace globalThis.crypto before constructing the client and observe generated values.
- Invariant to test: Security-critical randomness must not be taken from a mutable global at call time.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: stub globalThis.crypto with a deterministic implementation and assert generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex detects or refuses it.
