# Q1354: sms code request unbounded by target in MfaPasskeyApi.ts

## Question
MfaSmsApi.sendCode forwards the caller's input body; can an attacker direct the code to a number that is not the account's registered factor via MfaPasskeyApi.generateAuthenticationOptions?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Call sendCode with an arbitrary destination in the input.
- Invariant to test: The MFA delivery target must be server-selected from the enrolled factor, not caller-supplied.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: pass an arbitrary destination to MfaPasskeyApi.generateAuthenticationOptions and assert it is not included in the request.
