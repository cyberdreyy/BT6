# Q2361: delegated fallback path for on-device wallets in generateWalletIdempotencyKey.ts

## Question
addSessionSigners falls back to delegateWallets when the wallet is not TEE-backed; can an attacker use generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex to convert a session-signer request into a full delegation the user never approved?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Call the add path with an on-device wallet and an empty signers array.
- Invariant to test: A session-signer request must not silently become a delegation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex on an on-device wallet and assert the consent prompt describes delegation.
