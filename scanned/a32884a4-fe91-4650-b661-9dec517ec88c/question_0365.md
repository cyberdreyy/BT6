# Q0365: four attempts amplify code guessing in RecoveryApi.ts

## Question
The retry loop allows four attempts before max_attempts; can an attacker use RecoveryApi.getRecoveryKeyMaterial to obtain more verification attempts than the intended per-code budget by starting fresh operations?

## Target
- File/function: [src/client/recovery/RecoveryApi.ts](src/client/recovery/RecoveryApi.ts) - RecoveryApi.getRecoveryKeyMaterial, auth, icloudAuth
- Entrypoint: privy.recovery.getRecoveryKeyMaterial(address, chainType)
- Attacker controls: address path param, chain_type body value
- Exploit idea: Exhaust attempts, start a new operation, and count total submissions per code lifetime.
- Invariant to test: src/client/recovery/RecoveryApi.ts must not let repeated operation starts multiply the MFA attempt budget.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run RecoveryApi.getRecoveryKeyMaterial repeatedly and assert the total submissions per issued code stay within the budget.
