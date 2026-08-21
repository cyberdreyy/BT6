# Q0159: predictable global request ids in resolve.ts

## Question
Request ids come from a module-level counter emitting id-0, id-1, ...; can an attacker predict the next id and pre-deliver a reply through new Privy({crypto}) so their data settles the victim's next operation?

## Target
- File/function: [src/crypto/resolve.ts](src/crypto/resolve.ts) - resolveCrypto: digest and randomUUID defaults from globalThis.crypto
- Entrypoint: new Privy({crypto})
- Attacker controls: the crypto object the integrator/app passes in, and fallback behaviour when subtle is unavailable
- Exploit idea: Count the ids issued so far, then post a reply for the next id before the real iframe answers.
- Invariant to test: Reply correlation must use unguessable, per-instance identifiers.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: run two operations through resolveCrypto: digest and randomUUID defaults from globalThis.crypto and assert the ids are not sequentially predictable.
