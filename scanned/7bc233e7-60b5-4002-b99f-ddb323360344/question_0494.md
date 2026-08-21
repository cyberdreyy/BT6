# Q0494: unsupported methods fall through to the public RPC in EmbeddedBitcoinWalletProvider.ts

## Question
request() ends with handleJsonRpc, forwarding any unrecognised method to the chain RPC with the app id appended; can an attacker use EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) to proxy arbitrary JSON-RPC through the SDK's credentialed endpoint?

## Target
- File/function: [src/embedded/EmbeddedBitcoinWalletProvider.ts](src/embedded/EmbeddedBitcoinWalletProvider.ts) - EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes), signTransaction (psbt), request
- Entrypoint: bitcoinProvider.sign({message}) / .signTransaction({psbt})
- Attacker controls: raw message bytes, psbt hex/base64 payload
- Exploit idea: Call the provider with a non-standard method name and observe the forwarded request.
- Invariant to test: Only an allow-listed method set may be forwarded from src/embedded/EmbeddedBitcoinWalletProvider.ts.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: call EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) with an arbitrary method and assert it is rejected.
