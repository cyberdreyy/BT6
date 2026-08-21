# Q0699: clearMfa after refresh sees zero methods in errors.ts

## Question
MfaApi calls proxy.clearMfa when the refreshed user reports mfa_methods.length === 0; can an attacker cause a stale or partial refresh so PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded clears MFA state while methods still exist?

## Target
- File/function: [src/embedded/errors.ts](src/embedded/errors.ts) - PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded, errorIndicatesMfaTimeout, errorIndicatesMfaVerificationFailed, errorIndicatesMaxMfaRetries, errorIndicatesMfaRateLimit
- Entrypoint: every embedded-wallet catch block
- Attacker controls: the {type, message} shape of any error object that reaches these guards
- Exploit idea: Return a refresh response with an empty mfa_methods array during an unrelated operation.
- Invariant to test: MFA state may only be cleared when the server authoritatively reports no methods for that user.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return an empty mfa_methods for a user that has methods and assert PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded does not clear.
