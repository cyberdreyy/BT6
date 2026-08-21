# Q3447: recovery of a wallet the user does not own in RecoveryICloudApi.ts

## Question
_load recovers based on the passed entropyId and verifier; can an attacker pass an entropyId for another user's wallet through RecoveryICloudApi.init and trigger a recovery attempt against it?

## Target
- File/function: [src/client/recovery/RecoveryICloudApi.ts](src/client/recovery/RecoveryICloudApi.ts) - RecoveryICloudApi.init, getICloudConfiguration
- Entrypoint: privy.recovery.icloudAuth.init(clientType)
- Attacker controls: client_type value, response fields used as recovery configuration
- Exploit idea: Call the provider path with a foreign entropyId.
- Invariant to test: Entropy identifiers must be verified against the authenticated user's linked accounts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign entropyId to RecoveryICloudApi.init and assert it is rejected.
