# Q2035: connection object supplied by the caller in getWalletPublicKeyFromTransaction.ts

## Question
handleSignAndSendTransaction broadcasts with `connection.sendRawTransaction` taken from the request params; can an attacker pass a connection through every Solana signTransaction / signAndSendTransaction call that forwards the signed transaction somewhere else or reports a false signature?

## Target
- File/function: [src/solana/getWalletPublicKeyFromTransaction.ts](src/solana/getWalletPublicKeyFromTransaction.ts) - getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address
- Entrypoint: every Solana signTransaction / signAndSendTransaction call
- Attacker controls: transaction structure, versioned vs legacy, address-table lookups, duplicate/ordered keys
- Exploit idea: Call signAndSendTransaction with a hand-built connection object.
- Invariant to test: Broadcast transport must be SDK-controlled, not caller-supplied.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a stub connection to getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address and assert the SDK uses its own trusted transport.
