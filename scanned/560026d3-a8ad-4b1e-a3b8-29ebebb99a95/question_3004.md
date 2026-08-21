# Q3004: access token fetched before every mfa call in MfaPasskeyApi.ts

## Question
MfaApi.getAccessTokenInternal resolves a token per call; can an attacker swap the active session between the token fetch and the proxy call in MfaPasskeyApi.generateAuthenticationOptions so MFA is evaluated against a different identity?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Switch users between the two awaits.
- Invariant to test: MFA operations must pin one identity for their whole duration.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: switch identity mid-call in MfaPasskeyApi.generateAuthenticationOptions and assert the operation aborts.
