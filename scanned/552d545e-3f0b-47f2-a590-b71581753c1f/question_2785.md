# Q2785: password type check only in RecoveryApi.ts

## Question
create() rejects a non-string password but performs no strength or confirmation check; can an attacker set a trivial recovery password via RecoveryApi.getRecoveryKeyMaterial that later allows offline recovery?

## Target
- File/function: [src/client/recovery/RecoveryApi.ts](src/client/recovery/RecoveryApi.ts) - RecoveryApi.getRecoveryKeyMaterial, auth, icloudAuth
- Entrypoint: privy.recovery.getRecoveryKeyMaterial(address, chainType)
- Attacker controls: address path param, chain_type body value
- Exploit idea: Call create with a one-character password.
- Invariant to test: src/client/recovery/RecoveryApi.ts must enforce the app's recovery strength policy before provisioning.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call RecoveryApi.getRecoveryKeyMaterial with a weak password and assert the configured policy is enforced.
