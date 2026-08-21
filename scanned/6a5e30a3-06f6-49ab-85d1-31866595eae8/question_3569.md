# Q3569: solana create takes an ethereum account argument in resolve.ts

## Question
createSolana accepts an ethereumAccount whose provider is loaded first; can an attacker pass a foreign ethereum account through resolveCrypto: digest and randomUUID defaults from globalThis.crypto so entropy from another wallet is used for the new Solana wallet?

## Target
- File/function: [src/crypto/resolve.ts](src/crypto/resolve.ts) - resolveCrypto: digest and randomUUID defaults from globalThis.crypto
- Entrypoint: new Privy({crypto})
- Attacker controls: the crypto object the integrator/app passes in, and fallback behaviour when subtle is unavailable
- Exploit idea: Call createSolana with an ethereum account object that is not the user's.
- Invariant to test: Cross-chain wallet derivation must use only the authenticated user's own accounts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign ethereum account to resolveCrypto: digest and randomUUID defaults from globalThis.crypto and assert rejection.
