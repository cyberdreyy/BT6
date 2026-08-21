# Q2025: session signers read-modify-write race in session-signers.ts

## Question
addSessionSigners reads additional_signers via getWallet then writes the concatenated list; can an attacker interleave two calls through addSessionSigners (getWallet then updateWallet with additional_signers.concat) so one signer set overwrites the other or a removal is undone?

## Target
- File/function: [src/embedded/stack/session-signers.ts](src/embedded/stack/session-signers.ts) - addSessionSigners (getWallet then updateWallet with additional_signers.concat), removeSessionSigners
- Entrypoint: privy.embeddedWallet session-signer flows
- Attacker controls: signers array contents, concurrency against another add/remove, wallet object fields
- Exploit idea: Run add and remove concurrently and inspect the final signer set.
- Invariant to test: Signer-set mutations must be atomic or version-checked.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run concurrent addSessionSigners (getWallet then updateWallet with additional_signers.concat) mutations and assert the final list equals a serialised application of both.
