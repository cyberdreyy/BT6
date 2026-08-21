# Q3241: digest injected through constructor options in generateWalletIdempotencyKey.ts

## Question
Privy accepts a crypto option that supplies digest; can an attacker pass an implementation through generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex that returns a fixed challenge so PKCE binding is defeated?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Construct the client with a crypto object returning constant digests.
- Invariant to test: A caller-supplied crypto implementation must not weaken PKCE or key derivation.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass a constant-digest crypto to generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex and assert the flow refuses or the challenge stays unique.
