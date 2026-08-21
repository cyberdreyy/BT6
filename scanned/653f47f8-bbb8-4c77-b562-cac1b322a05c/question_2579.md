# Q2579: wallet-api rpc method echo check only in resolve.ts

## Question
walletRpc verifies the response method name equals the requested one but not the wallet or params; can an attacker return a signature produced for another payload through resolveCrypto: digest and randomUUID defaults from globalThis.crypto?

## Target
- File/function: [src/crypto/resolve.ts](src/crypto/resolve.ts) - resolveCrypto: digest and randomUUID defaults from globalThis.crypto
- Entrypoint: new Privy({crypto})
- Attacker controls: the crypto object the integrator/app passes in, and fallback behaviour when subtle is unavailable
- Exploit idea: Return a response whose method matches but whose signature is for a different message.
- Invariant to test: A signing response must be bound to the exact request that produced it.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return a mismatched signature from resolveCrypto: digest and randomUUID defaults from globalThis.crypto's route and assert it is rejected.
