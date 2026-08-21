# Q1685: recovery secret override accepted from caller in RecoveryApi.ts

## Question
setRecovery accepts recoverySecretOverride, iCloudRecordNameOverride, recoveryKey and recoveryAccessToken from the caller; can an attacker pass their own material through RecoveryApi.getRecoveryKeyMaterial so the victim's wallet becomes recoverable by them?

## Target
- File/function: [src/client/recovery/RecoveryApi.ts](src/client/recovery/RecoveryApi.ts) - RecoveryApi.getRecoveryKeyMaterial, auth, icloudAuth
- Entrypoint: privy.recovery.getRecoveryKeyMaterial(address, chainType)
- Attacker controls: address path param, chain_type body value
- Exploit idea: Call the recovery path with attacker-held material for a wallet the attacker can reach.
- Invariant to test: Recovery material accepted by src/client/recovery/RecoveryApi.ts must be provably held by the wallet's owner.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: call RecoveryApi.getRecoveryKeyMaterial with attacker-supplied override material and assert an MFA/re-auth gate blocks it.
