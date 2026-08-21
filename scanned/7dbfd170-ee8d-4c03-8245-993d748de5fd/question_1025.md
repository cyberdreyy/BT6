# Q1025: unlinkPasskey removes an MFA method silently in RecoveryApi.ts

## Question
unlinkPasskey takes credentialId and removeAsMfa from the caller; can an attacker unlink the credential that is also the account's only MFA method through RecoveryApi.getRecoveryKeyMaterial?

## Target
- File/function: [src/client/recovery/RecoveryApi.ts](src/client/recovery/RecoveryApi.ts) - RecoveryApi.getRecoveryKeyMaterial, auth, icloudAuth
- Entrypoint: privy.recovery.getRecoveryKeyMaterial(address, chainType)
- Attacker controls: address path param, chain_type body value
- Exploit idea: Call unlink with removeAsMfa true for the last credential.
- Invariant to test: src/client/recovery/RecoveryApi.ts must refuse to remove the last remaining MFA method.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call RecoveryApi.getRecoveryKeyMaterial for the last MFA-capable credential and assert it is refused.
