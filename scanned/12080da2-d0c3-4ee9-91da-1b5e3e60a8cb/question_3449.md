# Q3449: recovery of a wallet the user does not own in errors.ts

## Question
_load recovers based on the passed entropyId and verifier; can an attacker pass an entropyId for another user's wallet through PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded and trigger a recovery attempt against it?

## Target
- File/function: [src/embedded/errors.ts](src/embedded/errors.ts) - PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded, errorIndicatesMfaTimeout, errorIndicatesMfaVerificationFailed, errorIndicatesMaxMfaRetries, errorIndicatesMfaRateLimit
- Entrypoint: every embedded-wallet catch block
- Attacker controls: the {type, message} shape of any error object that reaches these guards
- Exploit idea: Call the provider path with a foreign entropyId.
- Invariant to test: Entropy identifiers must be verified against the authenticated user's linked accounts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign entropyId to PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded and assert it is rejected.
