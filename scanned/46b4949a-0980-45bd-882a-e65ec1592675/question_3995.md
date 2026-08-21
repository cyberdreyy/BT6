# Q3995: wallet not on device error swallows real failures in RecoveryApi.ts

## Question
The recovery branch is entered whenever the error type matches, even when the true cause differs; can an attacker cause RecoveryApi.getRecoveryKeyMaterial to run recovery instead of surfacing an authorization failure?

## Target
- File/function: [src/client/recovery/RecoveryApi.ts](src/client/recovery/RecoveryApi.ts) - RecoveryApi.getRecoveryKeyMaterial, auth, icloudAuth
- Entrypoint: privy.recovery.getRecoveryKeyMaterial(address, chainType)
- Attacker controls: address path param, chain_type body value
- Exploit idea: Return the recovery-needed type for an authorization error.
- Invariant to test: Authorization failures must never be converted into recovery attempts.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return the matching type for a 403-class failure and assert RecoveryApi.getRecoveryKeyMaterial does not recover.
