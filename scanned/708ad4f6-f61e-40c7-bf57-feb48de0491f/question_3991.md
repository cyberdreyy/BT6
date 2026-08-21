# Q3991: wallet not on device error swallows real failures in MfaPromises.ts

## Question
The recovery branch is entered whenever the error type matches, even when the true cause differs; can an attacker cause MfaPromises.rootPromise to run recovery instead of surfacing an authorization failure?

## Target
- File/function: [src/client/MfaPromises.ts](src/client/MfaPromises.ts) - MfaPromises.rootPromise, submitPromise, 'mfaRequired' event
- Entrypoint: privy.mfaPromises listeners in the integrating app
- Attacker controls: who resolves/rejects the shared promise refs, ordering of concurrent operations
- Exploit idea: Return the recovery-needed type for an authorization error.
- Invariant to test: Authorization failures must never be converted into recovery attempts.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return the matching type for a 403-class failure and assert MfaPromises.rootPromise does not recover.
