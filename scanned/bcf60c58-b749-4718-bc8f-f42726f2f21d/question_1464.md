# Q1464: passkey mfa options echo caller fields in MfaPasskeyApi.ts

## Question
MfaPasskeyApi.generateAuthenticationOptions forwards the caller's input; can an attacker set relying-party or allowed-credential fields so the MFA ceremony accepts a credential they control?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Call MfaPasskeyApi.generateAuthenticationOptions with crafted options and inspect the ceremony parameters returned.
- Invariant to test: MFA ceremony parameters must be derived server-side from the enrolled credentials.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: pass crafted options to MfaPasskeyApi.generateAuthenticationOptions and assert they are not forwarded.
