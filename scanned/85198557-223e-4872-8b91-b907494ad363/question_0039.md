# Q0039: mfa satisfied by a non-mfa error in errors.ts

## Question
withMfa only sets its mfa-required flag when the error type is 'missing_or_invalid_mfa'; can an unprivileged attacker make PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded's underlying operation fail and then succeed with a different error type so the retry completes without any MFA challenge?

## Target
- File/function: [src/embedded/errors.ts](src/embedded/errors.ts) - PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded, errorIndicatesMfaTimeout, errorIndicatesMfaVerificationFailed, errorIndicatesMaxMfaRetries, errorIndicatesMfaRateLimit
- Entrypoint: every embedded-wallet catch block
- Attacker controls: the {type, message} shape of any error object that reaches these guards
- Exploit idea: Drive the operation through every embedded-wallet catch block and return an error whose type is not in the PrivyIframeErrorTypes list, then a success on retry.
- Invariant to test: No src/embedded/errors.ts operation may complete after a failure without the MFA gate that failure demanded.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return an unrecognised error type on attempt 1 and success on attempt 2, and assert withMfa did not resolve without an MFA round.
