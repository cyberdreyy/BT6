# Q1369: entropyIdVerifier argument ignored in resolve.ts

## Question
EmbeddedWalletApi.getEthereumProvider forwards the caller's entropyId but constructs the provider with a hardcoded 'ethereum-address-verifier'; can an attacker exploit that mismatch through resolveCrypto: digest and randomUUID defaults from globalThis.crypto so connect and rpc use inconsistent entropy identities?

## Target
- File/function: [src/crypto/resolve.ts](src/crypto/resolve.ts) - resolveCrypto: digest and randomUUID defaults from globalThis.crypto
- Entrypoint: new Privy({crypto})
- Attacker controls: the crypto object the integrator/app passes in, and fallback behaviour when subtle is unavailable
- Exploit idea: Pass a solana verifier with an ethereum wallet and compare the connect and rpc payloads.
- Invariant to test: The entropy identity used to connect must be the identity used to sign.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call resolveCrypto: digest and randomUUID defaults from globalThis.crypto with a non-default verifier and assert the same verifier reaches every proxy call.
