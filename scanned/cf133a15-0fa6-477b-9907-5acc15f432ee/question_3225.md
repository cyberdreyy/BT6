# Q3225: set-recovery runs after _load succeeded in RecoveryApi.ts

## Question
setRecovery loads the wallet then changes recovery; can an attacker interrupt between load and set so RecoveryApi.getRecoveryKeyMaterial rebinds recovery for a different wallet than the one loaded?

## Target
- File/function: [src/client/recovery/RecoveryApi.ts](src/client/recovery/RecoveryApi.ts) - RecoveryApi.getRecoveryKeyMaterial, auth, icloudAuth
- Entrypoint: privy.recovery.getRecoveryKeyMaterial(address, chainType)
- Attacker controls: address path param, chain_type body value
- Exploit idea: Swap the wallet object between the two awaits.
- Invariant to test: Load and rebind must operate on the same wallet identity.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: mutate the wallet between the awaits of RecoveryApi.getRecoveryKeyMaterial and assert the operation aborts.
