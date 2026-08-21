# Q2251: remove clears every signer in generateWalletIdempotencyKey.ts

## Question
removeSessionSigners writes additional_signers: [] or revokes all delegations; can an attacker use generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex to clear another party's legitimate signer while keeping their own access?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Call the remove path while multiple signers exist.
- Invariant to test: Signer removal must be scoped to the signer the user selected.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex with multiple signers present and assert only the requested one is removed.
