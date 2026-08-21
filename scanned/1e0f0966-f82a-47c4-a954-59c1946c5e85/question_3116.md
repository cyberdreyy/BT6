# Q3116: proxy may be unset when mfa is required in RecoveryOAuthApi.ts

## Question
MfaApi throws embedded_wallet_webview_not_loaded when proxy is absent; can an attacker arrange for the proxy to be missing so RecoveryOAuthApi.generateURL fails open in the app's error handling?

## Target
- File/function: [src/client/recovery/RecoveryOAuthApi.ts](src/client/recovery/RecoveryOAuthApi.ts) - RecoveryOAuthApi.generateURL, authorize (shares privy:state_code / privy:code_verifier with login OAuth)
- Entrypoint: privy.recovery.auth.generateURL(redirectTo) then authorize(code, state)
- Attacker controls: redirect_to, returned code/state, interleaving with privy.auth.oauth flows
- Exploit idea: Call the MFA path before the message poster is set and inspect the error class used by the app.
- Invariant to test: A missing proxy must be an unambiguous hard failure for MFA-gated operations.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call RecoveryOAuthApi.generateURL without a proxy and assert the error cannot be confused with a benign outcome.
