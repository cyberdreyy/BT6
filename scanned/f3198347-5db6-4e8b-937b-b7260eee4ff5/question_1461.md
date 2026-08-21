# Q1461: passkey mfa options echo caller fields in MfaPromises.ts

## Question
MfaPasskeyApi.generateAuthenticationOptions forwards the caller's input; can an attacker set relying-party or allowed-credential fields so the MFA ceremony accepts a credential they control?

## Target
- File/function: [src/client/MfaPromises.ts](src/client/MfaPromises.ts) - MfaPromises.rootPromise, submitPromise, 'mfaRequired' event
- Entrypoint: privy.mfaPromises listeners in the integrating app
- Attacker controls: who resolves/rejects the shared promise refs, ordering of concurrent operations
- Exploit idea: Call MfaPromises.rootPromise with crafted options and inspect the ceremony parameters returned.
- Invariant to test: MFA ceremony parameters must be derived server-side from the enrolled credentials.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: pass crafted options to MfaPromises.rootPromise and assert they are not forwarded.
