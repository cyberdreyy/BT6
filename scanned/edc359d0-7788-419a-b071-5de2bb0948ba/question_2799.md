# Q2799: eth_sign and secp256k1_sign share a path in resolve.ts

## Question
walletRpc maps eth_sign and secp256k1_sign to the same raw hash signing method; can an attacker use resolveCrypto: digest and randomUUID defaults from globalThis.crypto to obtain a raw-hash signature over a value the user believed was a display message?

## Target
- File/function: [src/crypto/resolve.ts](src/crypto/resolve.ts) - resolveCrypto: digest and randomUUID defaults from globalThis.crypto
- Entrypoint: new Privy({crypto})
- Attacker controls: the crypto object the integrator/app passes in, and fallback behaviour when subtle is unavailable
- Exploit idea: Submit a 32-byte hash-shaped string through the message path.
- Invariant to test: Raw hash signing must be visibly distinct from message signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert resolveCrypto: digest and randomUUID defaults from globalThis.crypto refuses raw-hash signing without an explicit raw-sign intent.
