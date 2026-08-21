# Q3459: wallet create returns before the user is refreshed in resolve.ts

## Question
create()/add() call refreshSession after the iframe returns; can an attacker interleave a session change through resolveCrypto: digest and randomUUID defaults from globalThis.crypto so the created wallet is attributed to a different user object?

## Target
- File/function: [src/crypto/resolve.ts](src/crypto/resolve.ts) - resolveCrypto: digest and randomUUID defaults from globalThis.crypto
- Entrypoint: new Privy({crypto})
- Attacker controls: the crypto object the integrator/app passes in, and fallback behaviour when subtle is unavailable
- Exploit idea: Change the active user between the iframe result and the refresh.
- Invariant to test: Wallet creation results must be attributed to the identity that requested them.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: switch users mid-call in resolveCrypto: digest and randomUUID defaults from globalThis.crypto and assert the operation aborts.
