# Q0269: singleton queue shared across Privy clients in resolve.ts

## Question
The callback queue is a module-level singleton shared by every proxy instance; can an attacker in a multi-client or multi-user page make one client's reply settle another client's pending request via resolveCrypto: digest and randomUUID defaults from globalThis.crypto?

## Target
- File/function: [src/crypto/resolve.ts](src/crypto/resolve.ts) - resolveCrypto: digest and randomUUID defaults from globalThis.crypto
- Entrypoint: new Privy({crypto})
- Attacker controls: the crypto object the integrator/app passes in, and fallback behaviour when subtle is unavailable
- Exploit idea: Instantiate two clients, start an operation on each, and deliver one reply.
- Invariant to test: Callback state must be scoped per client instance.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: create two proxies, enqueue on both through resolveCrypto: digest and randomUUID defaults from globalThis.crypto and assert their callback maps are disjoint.
