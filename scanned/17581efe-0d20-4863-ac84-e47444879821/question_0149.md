# Q0149: mfaAlwaysRequired only on three operations in errors.ts

## Question
Only verifyMfa, unenrollMfa and unlinkPasskey are invoked with mfaAlwaysRequired; can an attacker reach a comparable privileged operation in src/embedded/errors.ts that skips the always-on gate?

## Target
- File/function: [src/embedded/errors.ts](src/embedded/errors.ts) - PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded, errorIndicatesMfaTimeout, errorIndicatesMfaVerificationFailed, errorIndicatesMaxMfaRetries, errorIndicatesMfaRateLimit
- Entrypoint: every embedded-wallet catch block
- Attacker controls: the {type, message} shape of any error object that reaches these guards
- Exploit idea: Enumerate the operations routed through invokeWithMfa and compare their flags.
- Invariant to test: Every operation that changes MFA state or produces a signature must be gated identically.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert each privileged operation reachable from PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded sets mfaAlwaysRequired.
