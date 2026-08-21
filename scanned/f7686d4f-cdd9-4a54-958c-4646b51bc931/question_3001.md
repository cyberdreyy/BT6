# Q3001: access token fetched before every mfa call in MfaPromises.ts

## Question
MfaApi.getAccessTokenInternal resolves a token per call; can an attacker swap the active session between the token fetch and the proxy call in MfaPromises.rootPromise so MFA is evaluated against a different identity?

## Target
- File/function: [src/client/MfaPromises.ts](src/client/MfaPromises.ts) - MfaPromises.rootPromise, submitPromise, 'mfaRequired' event
- Entrypoint: privy.mfaPromises listeners in the integrating app
- Attacker controls: who resolves/rejects the shared promise refs, ordering of concurrent operations
- Exploit idea: Switch users between the two awaits.
- Invariant to test: MFA operations must pin one identity for their whole duration.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: switch identity mid-call in MfaPromises.rootPromise and assert the operation aborts.
