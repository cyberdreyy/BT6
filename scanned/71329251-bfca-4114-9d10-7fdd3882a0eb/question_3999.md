# Q3999: wallet not on device error swallows real failures in errors.ts

## Question
The recovery branch is entered whenever the error type matches, even when the true cause differs; can an attacker cause PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded to run recovery instead of surfacing an authorization failure?

## Target
- File/function: [src/embedded/errors.ts](src/embedded/errors.ts) - PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded, errorIndicatesMfaTimeout, errorIndicatesMfaVerificationFailed, errorIndicatesMaxMfaRetries, errorIndicatesMfaRateLimit
- Entrypoint: every embedded-wallet catch block
- Attacker controls: the {type, message} shape of any error object that reaches these guards
- Exploit idea: Return the recovery-needed type for an authorization error.
- Invariant to test: Authorization failures must never be converted into recovery attempts.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return the matching type for a 403-class failure and assert PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded does not recover.
