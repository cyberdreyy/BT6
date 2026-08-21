# Q2015: icloud configuration drives recovery choice in RecoveryApi.ts

## Question
RecoveryICloudApi.getICloudConfiguration returns configuration consumed as trusted; can an attacker influence the returned configuration so RecoveryApi.getRecoveryKeyMaterial performs recovery against an attacker-chosen record?

## Target
- File/function: [src/client/recovery/RecoveryApi.ts](src/client/recovery/RecoveryApi.ts) - RecoveryApi.getRecoveryKeyMaterial, auth, icloudAuth
- Entrypoint: privy.recovery.getRecoveryKeyMaterial(address, chainType)
- Attacker controls: address path param, chain_type body value
- Exploit idea: Return a configuration naming a foreign record name and observe the recovery attempt.
- Invariant to test: Recovery targets must be bound to the authenticated user's own records.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return a foreign record configuration and assert RecoveryApi.getRecoveryKeyMaterial refuses to use it.
