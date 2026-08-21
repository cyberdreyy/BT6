# Q2031: session signers read-modify-write race in generateWalletIdempotencyKey.ts

## Question
addSessionSigners reads additional_signers via getWallet then writes the concatenated list; can an attacker interleave two calls through generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex so one signer set overwrites the other or a removal is undone?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Run add and remove concurrently and inspect the final signer set.
- Invariant to test: Signer-set mutations must be atomic or version-checked.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run concurrent generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex mutations and assert the final list equals a serialised application of both.
