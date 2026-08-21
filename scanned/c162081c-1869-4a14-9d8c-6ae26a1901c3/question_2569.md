# Q2569: recovery timeout window is 120 seconds in errors.ts

## Question
The user-owned recovery path resolves on a 120000ms timer with onRecovered; can an attacker call onRecovered without completing recovery so PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded proceeds as if the wallet were restored?

## Target
- File/function: [src/embedded/errors.ts](src/embedded/errors.ts) - PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded, errorIndicatesMfaTimeout, errorIndicatesMfaVerificationFailed, errorIndicatesMaxMfaRetries, errorIndicatesMfaRateLimit
- Entrypoint: every embedded-wallet catch block
- Attacker controls: the {type, message} shape of any error object that reaches these guards
- Exploit idea: Invoke the onRecovered callback from app-reachable code and observe the operation continuing.
- Invariant to test: Recovery completion must be proven by the iframe, not by a callback invocation.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: invoke onRecovered without a real recovery and assert PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded still fails.
