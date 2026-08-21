# Q1907: recovery key material fetched by address in RecoveryICloudApi.ts

## Question
RecoveryApi.getRecoveryKeyMaterial takes an address path param and chain_type body; can an attacker request material for an address that is not theirs through RecoveryICloudApi.init?

## Target
- File/function: [src/client/recovery/RecoveryICloudApi.ts](src/client/recovery/RecoveryICloudApi.ts) - RecoveryICloudApi.init, getICloudConfiguration
- Entrypoint: privy.recovery.icloudAuth.init(clientType)
- Attacker controls: client_type value, response fields used as recovery configuration
- Exploit idea: Call the method with another user's wallet address.
- Invariant to test: Recovery material requests must be scoped to wallets owned by the authenticated user.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: call RecoveryICloudApi.init with a foreign address and assert the SDK refuses before the request.
