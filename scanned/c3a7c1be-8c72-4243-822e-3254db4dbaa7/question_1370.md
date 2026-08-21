# Q1370: entropyIdVerifier argument ignored in encodings.ts

## Question
EmbeddedWalletApi.getEthereumProvider forwards the caller's entropyId but constructs the provider with a hardcoded 'ethereum-address-verifier'; can an attacker exploit that mismatch through base64 / utf8 conversions used for signing payloads and signatures so connect and rpc use inconsistent entropy identities?

## Target
- File/function: [src/utils/encodings.ts](src/utils/encodings.ts) - base64 / utf8 conversions used for signing payloads and signatures
- Entrypoint: generateAuthorizationSignature and every proxy signMessage payload
- Attacker controls: byte strings crossing the encode/decode boundary
- Exploit idea: Pass a solana verifier with an ethereum wallet and compare the connect and rpc payloads.
- Invariant to test: The entropy identity used to connect must be the identity used to sign.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call base64 / utf8 conversions used for signing payloads and signatures with a non-default verifier and assert the same verifier reaches every proxy call.
