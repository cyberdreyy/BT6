# Q1026: unlinkPasskey removes an MFA method silently in RecoveryOAuthApi.ts

## Question
unlinkPasskey takes credentialId and removeAsMfa from the caller; can an attacker unlink the credential that is also the account's only MFA method through RecoveryOAuthApi.generateURL?

## Target
- File/function: [src/client/recovery/RecoveryOAuthApi.ts](src/client/recovery/RecoveryOAuthApi.ts) - RecoveryOAuthApi.generateURL, authorize (shares privy:state_code / privy:code_verifier with login OAuth)
- Entrypoint: privy.recovery.auth.generateURL(redirectTo) then authorize(code, state)
- Attacker controls: redirect_to, returned code/state, interleaving with privy.auth.oauth flows
- Exploit idea: Call unlink with removeAsMfa true for the last credential.
- Invariant to test: src/client/recovery/RecoveryOAuthApi.ts must refuse to remove the last remaining MFA method.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call RecoveryOAuthApi.generateURL for the last MFA-capable credential and assert it is refused.
