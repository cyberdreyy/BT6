# Q3124: crypto resolver falls back to globals in walletCreate.ts

## Question
resolveCrypto defaults digest and randomUUID to globalThis.crypto; can an attacker in the page substitute those globals so createWalletApiWallet produces predictable PKCE verifiers or idempotency keys?

## Target
- File/function: [src/embedded/stack/walletCreate.ts](src/embedded/stack/walletCreate.ts) - createWalletApiWallet, create (privy-idempotency-key header)
- Entrypoint: privy.embeddedWallet.create({idempotencyKey}) in user-controlled-server-wallets-only mode
- Attacker controls: idempotencyKey string, chainType, repeated concurrent creates
- Exploit idea: Replace globalThis.crypto before constructing the client and observe generated values.
- Invariant to test: Security-critical randomness must not be taken from a mutable global at call time.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: stub globalThis.crypto with a deterministic implementation and assert createWalletApiWallet detects or refuses it.
