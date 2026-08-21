# Q1575: recovery upgrade path check is advisory in RecoveryApi.ts

## Question
throwIfInvalidRecoveryUpgradePath only rejects cloud-to-same-cloud upgrades; can an attacker use RecoveryApi.getRecoveryKeyMaterial to downgrade a strong recovery method (user-passcode) to a weaker attacker-controlled one?

## Target
- File/function: [src/client/recovery/RecoveryApi.ts](src/client/recovery/RecoveryApi.ts) - RecoveryApi.getRecoveryKeyMaterial, auth, icloudAuth
- Entrypoint: privy.recovery.getRecoveryKeyMaterial(address, chainType)
- Attacker controls: address path param, chain_type body value
- Exploit idea: Call setRecovery moving from user-passcode to a method whose secret the attacker supplies.
- Invariant to test: Recovery transitions must not weaken the custody of an existing wallet without re-authentication.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: enumerate every (current, target) pair through RecoveryApi.getRecoveryKeyMaterial and assert downgrades are refused.
