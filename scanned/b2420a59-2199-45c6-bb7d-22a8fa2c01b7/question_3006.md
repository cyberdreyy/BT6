# Q3006: access token fetched before every mfa call in RecoveryOAuthApi.ts

## Question
MfaApi.getAccessTokenInternal resolves a token per call; can an attacker swap the active session between the token fetch and the proxy call in RecoveryOAuthApi.generateURL so MFA is evaluated against a different identity?

## Target
- File/function: [src/client/recovery/RecoveryOAuthApi.ts](src/client/recovery/RecoveryOAuthApi.ts) - RecoveryOAuthApi.generateURL, authorize (shares privy:state_code / privy:code_verifier with login OAuth)
- Entrypoint: privy.recovery.auth.generateURL(redirectTo) then authorize(code, state)
- Attacker controls: redirect_to, returned code/state, interleaving with privy.auth.oauth flows
- Exploit idea: Switch users between the two awaits.
- Invariant to test: MFA operations must pin one identity for their whole duration.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: switch identity mid-call in RecoveryOAuthApi.generateURL and assert the operation aborts.
