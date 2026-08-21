# Q3899: ping doubles as a liveness oracle in resolve.ts

## Question
ping() invokes privy:iframe:ready with a caller-controlled timeout; can an attacker use resolveCrypto: digest and randomUUID defaults from globalThis.crypto to keep the ready state true while the iframe is actually serving a different session?

## Target
- File/function: [src/crypto/resolve.ts](src/crypto/resolve.ts) - resolveCrypto: digest and randomUUID defaults from globalThis.crypto
- Entrypoint: new Privy({crypto})
- Attacker controls: the crypto object the integrator/app passes in, and fallback behaviour when subtle is unavailable
- Exploit idea: Flip the iframe session and observe the cached ready flag.
- Invariant to test: Readiness must be invalidated when the underlying wallet session changes.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: change the session and assert resolveCrypto: digest and randomUUID defaults from globalThis.crypto re-verifies readiness.
