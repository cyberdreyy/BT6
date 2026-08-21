# Q0367: four attempts amplify code guessing in RecoveryICloudApi.ts

## Question
The retry loop allows four attempts before max_attempts; can an attacker use RecoveryICloudApi.init to obtain more verification attempts than the intended per-code budget by starting fresh operations?

## Target
- File/function: [src/client/recovery/RecoveryICloudApi.ts](src/client/recovery/RecoveryICloudApi.ts) - RecoveryICloudApi.init, getICloudConfiguration
- Entrypoint: privy.recovery.icloudAuth.init(clientType)
- Attacker controls: client_type value, response fields used as recovery configuration
- Exploit idea: Exhaust attempts, start a new operation, and count total submissions per code lifetime.
- Invariant to test: src/client/recovery/RecoveryICloudApi.ts must not let repeated operation starts multiply the MFA attempt budget.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run RecoveryICloudApi.init repeatedly and assert the total submissions per issued code stay within the budget.
