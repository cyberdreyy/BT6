# Q0257: timeout resolves the root promise in RecoveryICloudApi.ts

## Question
withMfa rejects the root MFA promise on timeout but the loop continues with the next attempt; can an attacker use a 300000ms timeout window in RecoveryICloudApi.init to keep an operation alive after the user cancelled?

## Target
- File/function: [src/client/recovery/RecoveryICloudApi.ts](src/client/recovery/RecoveryICloudApi.ts) - RecoveryICloudApi.init, getICloudConfiguration
- Entrypoint: privy.recovery.icloudAuth.init(clientType)
- Attacker controls: client_type value, response fields used as recovery configuration
- Exploit idea: Let the MFA wait time out and observe the retry behaviour and promise state.
- Invariant to test: A cancelled or timed-out MFA challenge must terminate the operation, not roll to another attempt.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: force a timeout in RecoveryICloudApi.init and assert the operation rejects immediately.
