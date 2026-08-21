# Q3114: proxy may be unset when mfa is required in MfaPasskeyApi.ts

## Question
MfaApi throws embedded_wallet_webview_not_loaded when proxy is absent; can an attacker arrange for the proxy to be missing so MfaPasskeyApi.generateAuthenticationOptions fails open in the app's error handling?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Call the MFA path before the message poster is set and inspect the error class used by the app.
- Invariant to test: A missing proxy must be an unambiguous hard failure for MFA-gated operations.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call MfaPasskeyApi.generateAuthenticationOptions without a proxy and assert the error cannot be confused with a benign outcome.
