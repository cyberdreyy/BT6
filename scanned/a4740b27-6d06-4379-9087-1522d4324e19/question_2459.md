# Q2459: mfa cancelled treated as success in errors.ts

## Question
errorIndicatesMfaCanceled checks error.code === 'mfa_canceled'; can an attacker make PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded treat a cancellation as a benign outcome so the calling app proceeds as if the operation was authorised?

## Target
- File/function: [src/embedded/errors.ts](src/embedded/errors.ts) - PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded, errorIndicatesMfaTimeout, errorIndicatesMfaVerificationFailed, errorIndicatesMaxMfaRetries, errorIndicatesMfaRateLimit
- Entrypoint: every embedded-wallet catch block
- Attacker controls: the {type, message} shape of any error object that reaches these guards
- Exploit idea: Cancel an MFA prompt mid-operation and inspect what the operation returns.
- Invariant to test: A cancelled MFA must produce a failure the app cannot mistake for approval.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: cancel during PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded and assert the returned promise rejects.
