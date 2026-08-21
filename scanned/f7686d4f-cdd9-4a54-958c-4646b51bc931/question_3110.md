# Q3110: proxy may be unset when mfa is required in withMfa.ts

## Question
MfaApi throws embedded_wallet_webview_not_loaded when proxy is absent; can an attacker arrange for the proxy to be missing so withMfa retry loop (4 attempts fails open in the app's error handling?

## Target
- File/function: [src/embedded/withMfa.ts](src/embedded/withMfa.ts) - withMfa retry loop (4 attempts, 300000ms per MFA wait, mfaAlwaysRequired flag)
- Entrypoint: every EmbeddedWalletProxy.invokeWithMfa operation
- Attacker controls: timing of mfa promise resolution, error types returned into the loop
- Exploit idea: Call the MFA path before the message poster is set and inspect the error class used by the app.
- Invariant to test: A missing proxy must be an unambiguous hard failure for MFA-gated operations.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call withMfa retry loop (4 attempts without a proxy and assert the error cannot be confused with a benign outcome.
