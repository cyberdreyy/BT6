# Q3227: set-recovery runs after _load succeeded in RecoveryICloudApi.ts

## Question
setRecovery loads the wallet then changes recovery; can an attacker interrupt between load and set so RecoveryICloudApi.init rebinds recovery for a different wallet than the one loaded?

## Target
- File/function: [src/client/recovery/RecoveryICloudApi.ts](src/client/recovery/RecoveryICloudApi.ts) - RecoveryICloudApi.init, getICloudConfiguration
- Entrypoint: privy.recovery.icloudAuth.init(clientType)
- Attacker controls: client_type value, response fields used as recovery configuration
- Exploit idea: Swap the wallet object between the two awaits.
- Invariant to test: Load and rebind must operate on the same wallet identity.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: mutate the wallet between the awaits of RecoveryICloudApi.init and assert the operation aborts.
