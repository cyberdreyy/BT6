# Q0917: unenroll requires only the current session in RecoveryICloudApi.ts

## Question
unenrollMfa is gated by MFA but not by re-authentication; can an attacker with a live but unattended session use RecoveryICloudApi.init to remove the victim's second factor and then perform signing?

## Target
- File/function: [src/client/recovery/RecoveryICloudApi.ts](src/client/recovery/RecoveryICloudApi.ts) - RecoveryICloudApi.init, getICloudConfiguration
- Entrypoint: privy.recovery.icloudAuth.init(clientType)
- Attacker controls: client_type value, response fields used as recovery configuration
- Exploit idea: Run unenroll on a warm session and follow with a signing operation.
- Invariant to test: Removing a second factor must require a fresh, explicit user authentication.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run RecoveryICloudApi.init then a signature and assert the signature still demands MFA.
