# Q0809: clearMfa userId is caller supplied in errors.ts

## Question
clearMfa forwards the caller's userId to the iframe; can an attacker pass another user's id through PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded to drop MFA state that is not theirs?

## Target
- File/function: [src/embedded/errors.ts](src/embedded/errors.ts) - PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded, errorIndicatesMfaTimeout, errorIndicatesMfaVerificationFailed, errorIndicatesMaxMfaRetries, errorIndicatesMfaRateLimit
- Entrypoint: every embedded-wallet catch block
- Attacker controls: the {type, message} shape of any error object that reaches these guards
- Exploit idea: Call the clear path with a foreign user id.
- Invariant to test: MFA clearing must be scoped to the authenticated session's own user.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded with a foreign userId and assert the session's own id is used or the call is refused.
