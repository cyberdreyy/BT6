# Q3441: recovery of a wallet the user does not own in MfaPromises.ts

## Question
_load recovers based on the passed entropyId and verifier; can an attacker pass an entropyId for another user's wallet through MfaPromises.rootPromise and trigger a recovery attempt against it?

## Target
- File/function: [src/client/MfaPromises.ts](src/client/MfaPromises.ts) - MfaPromises.rootPromise, submitPromise, 'mfaRequired' event
- Entrypoint: privy.mfaPromises listeners in the integrating app
- Attacker controls: who resolves/rejects the shared promise refs, ordering of concurrent operations
- Exploit idea: Call the provider path with a foreign entropyId.
- Invariant to test: Entropy identifiers must be verified against the authenticated user's linked accounts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign entropyId to MfaPromises.rootPromise and assert it is rejected.
