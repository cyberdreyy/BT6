# Q3997: wallet not on device error swallows real failures in RecoveryICloudApi.ts

## Question
The recovery branch is entered whenever the error type matches, even when the true cause differs; can an attacker cause RecoveryICloudApi.init to run recovery instead of surfacing an authorization failure?

## Target
- File/function: [src/client/recovery/RecoveryICloudApi.ts](src/client/recovery/RecoveryICloudApi.ts) - RecoveryICloudApi.init, getICloudConfiguration
- Entrypoint: privy.recovery.icloudAuth.init(clientType)
- Attacker controls: client_type value, response fields used as recovery configuration
- Exploit idea: Return the recovery-needed type for an authorization error.
- Invariant to test: Authorization failures must never be converted into recovery attempts.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return the matching type for a 403-class failure and assert RecoveryICloudApi.init does not recover.
