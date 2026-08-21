# Q3443: recovery of a wallet the user does not own in MfaSmsApi.ts

## Question
_load recovers based on the passed entropyId and verifier; can an attacker pass an entropyId for another user's wallet through MfaSmsApi.sendCode and trigger a recovery attempt against it?

## Target
- File/function: [src/client/mfa/MfaSmsApi.ts](src/client/mfa/MfaSmsApi.ts) - MfaSmsApi.sendCode
- Entrypoint: privy.mfa.sms.sendCode(input)
- Attacker controls: phone/target fields in the input body, repetition
- Exploit idea: Call the provider path with a foreign entropyId.
- Invariant to test: Entropy identifiers must be verified against the authenticated user's linked accounts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign entropyId to MfaSmsApi.sendCode and assert it is rejected.
