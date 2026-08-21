# Q1905: recovery key material fetched by address in RecoveryApi.ts

## Question
RecoveryApi.getRecoveryKeyMaterial takes an address path param and chain_type body; can an attacker request material for an address that is not theirs through RecoveryApi.getRecoveryKeyMaterial?

## Target
- File/function: [src/client/recovery/RecoveryApi.ts](src/client/recovery/RecoveryApi.ts) - RecoveryApi.getRecoveryKeyMaterial, auth, icloudAuth
- Entrypoint: privy.recovery.getRecoveryKeyMaterial(address, chainType)
- Attacker controls: address path param, chain_type body value
- Exploit idea: Call the method with another user's wallet address.
- Invariant to test: Recovery material requests must be scoped to wallets owned by the authenticated user.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: call RecoveryApi.getRecoveryKeyMaterial with a foreign address and assert the SDK refuses before the request.
