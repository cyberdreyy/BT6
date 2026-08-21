# Q1909: recovery key material fetched by address in errors.ts

## Question
RecoveryApi.getRecoveryKeyMaterial takes an address path param and chain_type body; can an attacker request material for an address that is not theirs through PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded?

## Target
- File/function: [src/embedded/errors.ts](src/embedded/errors.ts) - PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded, errorIndicatesMfaTimeout, errorIndicatesMfaVerificationFailed, errorIndicatesMaxMfaRetries, errorIndicatesMfaRateLimit
- Entrypoint: every embedded-wallet catch block
- Attacker controls: the {type, message} shape of any error object that reaches these guards
- Exploit idea: Call the method with another user's wallet address.
- Invariant to test: Recovery material requests must be scoped to wallets owned by the authenticated user.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: call PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded with a foreign address and assert the SDK refuses before the request.
