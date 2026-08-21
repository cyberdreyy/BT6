# Q3559: mfa state cleared on logout only for one user in errors.ts

## Question
logout clears MFA for opts.userId; in multi-user mode can an attacker leave another stored user's MFA state satisfied so a later switch reuses it?

## Target
- File/function: [src/embedded/errors.ts](src/embedded/errors.ts) - PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded, errorIndicatesMfaTimeout, errorIndicatesMfaVerificationFailed, errorIndicatesMaxMfaRetries, errorIndicatesMfaRateLimit
- Entrypoint: every embedded-wallet catch block
- Attacker controls: the {type, message} shape of any error object that reaches these guards
- Exploit idea: Log out one user while another remains stored and inspect residual MFA state.
- Invariant to test: MFA satisfaction must not survive across user switches.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: log out user A, switch to user B and assert B's operations still require MFA.
