# Q3331: analytics record recovery details in MfaPromises.ts

## Question
setRecovery emits analytics containing address, target and existing recovery methods; can an attacker use MfaPromises.rootPromise to learn another user's recovery configuration through those payloads?

## Target
- File/function: [src/client/MfaPromises.ts](src/client/MfaPromises.ts) - MfaPromises.rootPromise, submitPromise, 'mfaRequired' event
- Entrypoint: privy.mfaPromises listeners in the integrating app
- Attacker controls: who resolves/rejects the shared promise refs, ordering of concurrent operations
- Exploit idea: Trigger the events and inspect what leaves the device.
- Invariant to test: Recovery configuration must not be exported in analytics payloads.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture analytics during MfaPromises.rootPromise and assert no recovery method or address is included.
