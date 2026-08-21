# Q0369: four attempts amplify code guessing in errors.ts

## Question
The retry loop allows four attempts before max_attempts; can an attacker use PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded to obtain more verification attempts than the intended per-code budget by starting fresh operations?

## Target
- File/function: [src/embedded/errors.ts](src/embedded/errors.ts) - PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded, errorIndicatesMfaTimeout, errorIndicatesMfaVerificationFailed, errorIndicatesMaxMfaRetries, errorIndicatesMfaRateLimit
- Entrypoint: every embedded-wallet catch block
- Attacker controls: the {type, message} shape of any error object that reaches these guards
- Exploit idea: Exhaust attempts, start a new operation, and count total submissions per code lifetime.
- Invariant to test: src/embedded/errors.ts must not let repeated operation starts multiply the MFA attempt budget.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded repeatedly and assert the total submissions per issued code stay within the budget.
