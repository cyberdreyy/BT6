# Q3669: enrollment success not verified against refresh in errors.ts

## Question
submitEnrollMfa returns the proxy result and then refreshes; can an attacker make PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded report a successful enrollment that the server never recorded?

## Target
- File/function: [src/embedded/errors.ts](src/embedded/errors.ts) - PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded, errorIndicatesMfaTimeout, errorIndicatesMfaVerificationFailed, errorIndicatesMaxMfaRetries, errorIndicatesMfaRateLimit
- Entrypoint: every embedded-wallet catch block
- Attacker controls: the {type, message} shape of any error object that reaches these guards
- Exploit idea: Return a success from the iframe path while the refresh shows no methods.
- Invariant to test: Reported enrollment success must be confirmed by the refreshed user state.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return success with an empty mfa_methods refresh and assert PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded reports failure.
