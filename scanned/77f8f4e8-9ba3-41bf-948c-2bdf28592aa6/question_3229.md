# Q3229: set-recovery runs after _load succeeded in errors.ts

## Question
setRecovery loads the wallet then changes recovery; can an attacker interrupt between load and set so PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded rebinds recovery for a different wallet than the one loaded?

## Target
- File/function: [src/embedded/errors.ts](src/embedded/errors.ts) - PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded, errorIndicatesMfaTimeout, errorIndicatesMfaVerificationFailed, errorIndicatesMaxMfaRetries, errorIndicatesMfaRateLimit
- Entrypoint: every embedded-wallet catch block
- Attacker controls: the {type, message} shape of any error object that reaches these guards
- Exploit idea: Swap the wallet object between the two awaits.
- Invariant to test: Load and rebind must operate on the same wallet identity.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: mutate the wallet between the awaits of PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded and assert the operation aborts.
