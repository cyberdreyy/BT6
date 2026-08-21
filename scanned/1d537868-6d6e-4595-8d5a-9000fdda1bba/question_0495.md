# Q0495: unsupported methods fall through to the public RPC in getWalletPublicKeyFromTransaction.ts

## Question
request() ends with handleJsonRpc, forwarding any unrecognised method to the chain RPC with the app id appended; can an attacker use getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address to proxy arbitrary JSON-RPC through the SDK's credentialed endpoint?

## Target
- File/function: [src/solana/getWalletPublicKeyFromTransaction.ts](src/solana/getWalletPublicKeyFromTransaction.ts) - getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address
- Entrypoint: every Solana signTransaction / signAndSendTransaction call
- Attacker controls: transaction structure, versioned vs legacy, address-table lookups, duplicate/ordered keys
- Exploit idea: Call the provider with a non-standard method name and observe the forwarded request.
- Invariant to test: Only an allow-listed method set may be forwarded from src/solana/getWalletPublicKeyFromTransaction.ts.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: call getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address with an arbitrary method and assert it is rejected.
