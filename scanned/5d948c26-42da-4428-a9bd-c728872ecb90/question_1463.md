# Q1463: passkey mfa options echo caller fields in MfaSmsApi.ts

## Question
MfaPasskeyApi.generateAuthenticationOptions forwards the caller's input; can an attacker set relying-party or allowed-credential fields so the MFA ceremony accepts a credential they control?

## Target
- File/function: [src/client/mfa/MfaSmsApi.ts](src/client/mfa/MfaSmsApi.ts) - MfaSmsApi.sendCode
- Entrypoint: privy.mfa.sms.sendCode(input)
- Attacker controls: phone/target fields in the input body, repetition
- Exploit idea: Call MfaSmsApi.sendCode with crafted options and inspect the ceremony parameters returned.
- Invariant to test: MFA ceremony parameters must be derived server-side from the enrolled credentials.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: pass crafted options to MfaSmsApi.sendCode and assert they are not forwarded.
