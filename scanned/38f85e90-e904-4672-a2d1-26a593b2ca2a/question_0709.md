# Q0709: invoke cache keyed by event plus payload in resolve.ts

## Question
invoke() caches in-flight promises for privy:wallet:create and privy:solana-wallet:create keyed by event+JSON(data); can an attacker replay identical arguments through resolveCrypto: digest and randomUUID defaults from globalThis.crypto so a second create silently returns the first result?

## Target
- File/function: [src/crypto/resolve.ts](src/crypto/resolve.ts) - resolveCrypto: digest and randomUUID defaults from globalThis.crypto
- Entrypoint: new Privy({crypto})
- Attacker controls: the crypto object the integrator/app passes in, and fallback behaviour when subtle is unavailable
- Exploit idea: Call the create path twice with identical arguments and observe one iframe round trip.
- Invariant to test: Cached in-flight results must not merge two distinct user-intent operations.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call resolveCrypto: digest and randomUUID defaults from globalThis.crypto twice with identical data and assert either two round trips or an explicit dedupe contract.
