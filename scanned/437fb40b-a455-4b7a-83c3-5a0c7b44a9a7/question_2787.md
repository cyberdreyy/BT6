# Q2787: password type check only in RecoveryICloudApi.ts

## Question
create() rejects a non-string password but performs no strength or confirmation check; can an attacker set a trivial recovery password via RecoveryICloudApi.init that later allows offline recovery?

## Target
- File/function: [src/client/recovery/RecoveryICloudApi.ts](src/client/recovery/RecoveryICloudApi.ts) - RecoveryICloudApi.init, getICloudConfiguration
- Entrypoint: privy.recovery.icloudAuth.init(clientType)
- Attacker controls: client_type value, response fields used as recovery configuration
- Exploit idea: Call create with a one-character password.
- Invariant to test: src/client/recovery/RecoveryICloudApi.ts must enforce the app's recovery strength policy before provisioning.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call RecoveryICloudApi.init with a weak password and assert the configured policy is enforced.
