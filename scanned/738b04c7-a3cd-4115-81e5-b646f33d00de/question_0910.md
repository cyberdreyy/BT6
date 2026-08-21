# Q0910: unenroll requires only the current session in withMfa.ts

## Question
unenrollMfa is gated by MFA but not by re-authentication; can an attacker with a live but unattended session use withMfa retry loop (4 attempts to remove the victim's second factor and then perform signing?

## Target
- File/function: [src/embedded/withMfa.ts](src/embedded/withMfa.ts) - withMfa retry loop (4 attempts, 300000ms per MFA wait, mfaAlwaysRequired flag)
- Entrypoint: every EmbeddedWalletProxy.invokeWithMfa operation
- Attacker controls: timing of mfa promise resolution, error types returned into the loop
- Exploit idea: Run unenroll on a warm session and follow with a signing operation.
- Invariant to test: Removing a second factor must require a fresh, explicit user authentication.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run withMfa retry loop (4 attempts then a signature and assert the signature still demands MFA.
