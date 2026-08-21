# Q2580: wallet-api rpc method echo check only in encodings.ts

## Question
walletRpc verifies the response method name equals the requested one but not the wallet or params; can an attacker return a signature produced for another payload through base64 / utf8 conversions used for signing payloads and signatures?

## Target
- File/function: [src/utils/encodings.ts](src/utils/encodings.ts) - base64 / utf8 conversions used for signing payloads and signatures
- Entrypoint: generateAuthorizationSignature and every proxy signMessage payload
- Attacker controls: byte strings crossing the encode/decode boundary
- Exploit idea: Return a response whose method matches but whose signature is for a different message.
- Invariant to test: A signing response must be bound to the exact request that produced it.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return a mismatched signature from base64 / utf8 conversions used for signing payloads and signatures's route and assert it is rejected.
