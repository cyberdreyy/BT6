# Q1577: recovery upgrade path check is advisory in RecoveryICloudApi.ts

## Question
throwIfInvalidRecoveryUpgradePath only rejects cloud-to-same-cloud upgrades; can an attacker use RecoveryICloudApi.init to downgrade a strong recovery method (user-passcode) to a weaker attacker-controlled one?

## Target
- File/function: [src/client/recovery/RecoveryICloudApi.ts](src/client/recovery/RecoveryICloudApi.ts) - RecoveryICloudApi.init, getICloudConfiguration
- Entrypoint: privy.recovery.icloudAuth.init(clientType)
- Attacker controls: client_type value, response fields used as recovery configuration
- Exploit idea: Call setRecovery moving from user-passcode to a method whose secret the attacker supplies.
- Invariant to test: Recovery transitions must not weaken the custody of an existing wallet without re-authentication.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: enumerate every (current, target) pair through RecoveryICloudApi.init and assert downgrades are refused.
