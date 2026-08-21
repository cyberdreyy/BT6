# Q1024: unlinkPasskey removes an MFA method silently in MfaPasskeyApi.ts

## Question
unlinkPasskey takes credentialId and removeAsMfa from the caller; can an attacker unlink the credential that is also the account's only MFA method through MfaPasskeyApi.generateAuthenticationOptions?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Call unlink with removeAsMfa true for the last credential.
- Invariant to test: src/client/mfa/MfaPasskeyApi.ts must refuse to remove the last remaining MFA method.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call MfaPasskeyApi.generateAuthenticationOptions for the last MFA-capable credential and assert it is refused.
