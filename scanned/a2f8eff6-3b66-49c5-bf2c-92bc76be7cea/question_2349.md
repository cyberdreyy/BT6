# Q2349: rate limit detection by message substring in errors.ts

## Question
errorIndicatesMfaRateLimit matches 'code 429' inside the message; can an attacker craft an error message containing that substring so PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded takes the rate-limited branch and suppresses a real failure?

## Target
- File/function: [src/embedded/errors.ts](src/embedded/errors.ts) - PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded, errorIndicatesMfaTimeout, errorIndicatesMfaVerificationFailed, errorIndicatesMaxMfaRetries, errorIndicatesMfaRateLimit
- Entrypoint: every embedded-wallet catch block
- Attacker controls: the {type, message} shape of any error object that reaches these guards
- Exploit idea: Return an error whose message embeds the substring.
- Invariant to test: Control-flow decisions must not depend on substring matching of error messages.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return an error message containing 'code 429' from a different cause and assert PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded does not treat it as rate limiting.
