# Q1023: unlinkPasskey removes an MFA method silently in MfaSmsApi.ts

## Question
unlinkPasskey takes credentialId and removeAsMfa from the caller; can an attacker unlink the credential that is also the account's only MFA method through MfaSmsApi.sendCode?

## Target
- File/function: [src/client/mfa/MfaSmsApi.ts](src/client/mfa/MfaSmsApi.ts) - MfaSmsApi.sendCode
- Entrypoint: privy.mfa.sms.sendCode(input)
- Attacker controls: phone/target fields in the input body, repetition
- Exploit idea: Call unlink with removeAsMfa true for the last credential.
- Invariant to test: src/client/mfa/MfaSmsApi.ts must refuse to remove the last remaining MFA method.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call MfaSmsApi.sendCode for the last MFA-capable credential and assert it is refused.
