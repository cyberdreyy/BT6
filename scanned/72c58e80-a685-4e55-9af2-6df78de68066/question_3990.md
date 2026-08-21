# Q3990: wallet not on device error swallows real failures in withMfa.ts

## Question
The recovery branch is entered whenever the error type matches, even when the true cause differs; can an attacker cause withMfa retry loop (4 attempts to run recovery instead of surfacing an authorization failure?

## Target
- File/function: [src/embedded/withMfa.ts](src/embedded/withMfa.ts) - withMfa retry loop (4 attempts, 300000ms per MFA wait, mfaAlwaysRequired flag)
- Entrypoint: every EmbeddedWalletProxy.invokeWithMfa operation
- Attacker controls: timing of mfa promise resolution, error types returned into the loop
- Exploit idea: Return the recovery-needed type for an authorization error.
- Invariant to test: Authorization failures must never be converted into recovery attempts.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return the matching type for a 403-class failure and assert withMfa retry loop (4 attempts does not recover.
