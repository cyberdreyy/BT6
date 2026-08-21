# Q1022: unlinkPasskey removes an MFA method silently in MfaApi.ts

## Question
unlinkPasskey takes credentialId and removeAsMfa from the caller; can an attacker unlink the credential that is also the account's only MFA method through MfaApi.verifyMfa?

## Target
- File/function: [src/client/mfa/MfaApi.ts](src/client/mfa/MfaApi.ts) - MfaApi.verifyMfa, initEnrollMfa, submitEnrollMfa, unenrollMfa, unlinkPasskey, clearMfa
- Entrypoint: privy.mfa.unenrollMfa(method) / privy.mfa.clearMfa({userId})
- Attacker controls: method argument, credentialId, removeAsMfa, userId, call ordering against refreshSession
- Exploit idea: Call unlink with removeAsMfa true for the last credential.
- Invariant to test: src/client/mfa/MfaApi.ts must refuse to remove the last remaining MFA method.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call MfaApi.verifyMfa for the last MFA-capable credential and assert it is refused.
