# Q1467: passkey mfa options echo caller fields in RecoveryICloudApi.ts

## Question
MfaPasskeyApi.generateAuthenticationOptions forwards the caller's input; can an attacker set relying-party or allowed-credential fields so the MFA ceremony accepts a credential they control?

## Target
- File/function: [src/client/recovery/RecoveryICloudApi.ts](src/client/recovery/RecoveryICloudApi.ts) - RecoveryICloudApi.init, getICloudConfiguration
- Entrypoint: privy.recovery.icloudAuth.init(clientType)
- Attacker controls: client_type value, response fields used as recovery configuration
- Exploit idea: Call RecoveryICloudApi.init with crafted options and inspect the ceremony parameters returned.
- Invariant to test: MFA ceremony parameters must be derived server-side from the enrolled credentials.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: pass crafted options to RecoveryICloudApi.init and assert they are not forwarded.
