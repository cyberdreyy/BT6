# Q1149: 15 second race leaves the callback registered in resolve.ts

## Question
The timeout helper rejects the caller but never dequeues the callback; can an attacker deliver a late reply through resolveCrypto: digest and randomUUID defaults from globalThis.crypto that settles a callback whose caller already gave up, corrupting later state?

## Target
- File/function: [src/crypto/resolve.ts](src/crypto/resolve.ts) - resolveCrypto: digest and randomUUID defaults from globalThis.crypto
- Entrypoint: new Privy({crypto})
- Attacker controls: the crypto object the integrator/app passes in, and fallback behaviour when subtle is unavailable
- Exploit idea: Let an operation time out, then deliver the reply.
- Invariant to test: A timed-out operation must remove its callback so late replies are discarded.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: time out an operation from resolveCrypto: digest and randomUUID defaults from globalThis.crypto, deliver the late reply and assert it is ignored.
