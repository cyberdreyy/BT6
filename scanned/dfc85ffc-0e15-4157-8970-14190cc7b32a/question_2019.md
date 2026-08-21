# Q2019: icloud configuration drives recovery choice in errors.ts

## Question
RecoveryICloudApi.getICloudConfiguration returns configuration consumed as trusted; can an attacker influence the returned configuration so PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded performs recovery against an attacker-chosen record?

## Target
- File/function: [src/embedded/errors.ts](src/embedded/errors.ts) - PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded, errorIndicatesMfaTimeout, errorIndicatesMfaVerificationFailed, errorIndicatesMaxMfaRetries, errorIndicatesMfaRateLimit
- Entrypoint: every embedded-wallet catch block
- Attacker controls: the {type, message} shape of any error object that reaches these guards
- Exploit idea: Return a configuration naming a foreign record name and observe the recovery attempt.
- Invariant to test: Recovery targets must be bound to the authenticated user's own records.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return a foreign record configuration and assert PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded refuses to use it.
