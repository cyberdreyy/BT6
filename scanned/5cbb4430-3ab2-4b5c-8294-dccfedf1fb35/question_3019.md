# Q3019: solana rpc path only implements signMessage in resolve.ts

## Question
walletRpc's solana branch handles signMessage and returns undefined for anything else; can an attacker exploit the undefined return in resolveCrypto: digest and randomUUID defaults from globalThis.crypto so a caller treats a failed operation as success?

## Target
- File/function: [src/crypto/resolve.ts](src/crypto/resolve.ts) - resolveCrypto: digest and randomUUID defaults from globalThis.crypto
- Entrypoint: new Privy({crypto})
- Attacker controls: the crypto object the integrator/app passes in, and fallback behaviour when subtle is unavailable
- Exploit idea: Call an unsupported solana method and inspect the resolved value.
- Invariant to test: Unsupported operations must reject rather than resolve undefined.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call an unsupported method through resolveCrypto: digest and randomUUID defaults from globalThis.crypto and assert it rejects.
