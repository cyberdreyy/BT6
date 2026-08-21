# Q3002: access token fetched before every mfa call in MfaApi.ts

## Question
MfaApi.getAccessTokenInternal resolves a token per call; can an attacker swap the active session between the token fetch and the proxy call in MfaApi.verifyMfa so MFA is evaluated against a different identity?

## Target
- File/function: [src/client/mfa/MfaApi.ts](src/client/mfa/MfaApi.ts) - MfaApi.verifyMfa, initEnrollMfa, submitEnrollMfa, unenrollMfa, unlinkPasskey, clearMfa
- Entrypoint: privy.mfa.unenrollMfa(method) / privy.mfa.clearMfa({userId})
- Attacker controls: method argument, credentialId, removeAsMfa, userId, call ordering against refreshSession
- Exploit idea: Switch users between the two awaits.
- Invariant to test: MFA operations must pin one identity for their whole duration.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: switch identity mid-call in MfaApi.verifyMfa and assert the operation aborts.
