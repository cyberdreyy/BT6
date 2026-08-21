# Q1027: unlinkPasskey removes an MFA method silently in RecoveryICloudApi.ts

## Question
unlinkPasskey takes credentialId and removeAsMfa from the caller; can an attacker unlink the credential that is also the account's only MFA method through RecoveryICloudApi.init?

## Target
- File/function: [src/client/recovery/RecoveryICloudApi.ts](src/client/recovery/RecoveryICloudApi.ts) - RecoveryICloudApi.init, getICloudConfiguration
- Entrypoint: privy.recovery.icloudAuth.init(clientType)
- Attacker controls: client_type value, response fields used as recovery configuration
- Exploit idea: Call unlink with removeAsMfa true for the last credential.
- Invariant to test: src/client/recovery/RecoveryICloudApi.ts must refuse to remove the last remaining MFA method.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call RecoveryICloudApi.init for the last MFA-capable credential and assert it is refused.
