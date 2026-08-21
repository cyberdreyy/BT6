# Q3119: proxy may be unset when mfa is required in errors.ts

## Question
MfaApi throws embedded_wallet_webview_not_loaded when proxy is absent; can an attacker arrange for the proxy to be missing so PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded fails open in the app's error handling?

## Target
- File/function: [src/embedded/errors.ts](src/embedded/errors.ts) - PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded, errorIndicatesMfaTimeout, errorIndicatesMfaVerificationFailed, errorIndicatesMaxMfaRetries, errorIndicatesMfaRateLimit
- Entrypoint: every embedded-wallet catch block
- Attacker controls: the {type, message} shape of any error object that reaches these guards
- Exploit idea: Call the MFA path before the message poster is set and inspect the error class used by the app.
- Invariant to test: A missing proxy must be an unambiguous hard failure for MFA-gated operations.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded without a proxy and assert the error cannot be confused with a benign outcome.
