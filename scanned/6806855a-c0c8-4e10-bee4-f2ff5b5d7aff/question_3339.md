# Q3339: analytics record recovery details in errors.ts

## Question
setRecovery emits analytics containing address, target and existing recovery methods; can an attacker use PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded to learn another user's recovery configuration through those payloads?

## Target
- File/function: [src/embedded/errors.ts](src/embedded/errors.ts) - PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded, errorIndicatesMfaTimeout, errorIndicatesMfaVerificationFailed, errorIndicatesMaxMfaRetries, errorIndicatesMfaRateLimit
- Entrypoint: every embedded-wallet catch block
- Attacker controls: the {type, message} shape of any error object that reaches these guards
- Exploit idea: Trigger the events and inspect what leaves the device.
- Invariant to test: Recovery configuration must not be exported in analytics payloads.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture analytics during PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded and assert no recovery method or address is included.
