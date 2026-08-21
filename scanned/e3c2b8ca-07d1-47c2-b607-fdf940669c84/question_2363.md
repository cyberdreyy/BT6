# Q2363: off-chain domain truncated to 32 bytes in EmbeddedSolanaWalletProvider.ts

## Question
deriveSolanaApplicationDomain copies the first 32 UTF-8 bytes of the origin into the application domain; can an attacker register a longer origin that collides with the victim's origin after truncation so EmbeddedSolanaWalletProvider.request produces messages the victim's verifier accepts?

## Target
- File/function: [src/embedded/EmbeddedSolanaWalletProvider.ts](src/embedded/EmbeddedSolanaWalletProvider.ts) - EmbeddedSolanaWalletProvider.request, handleSignTransaction, handleSignAndSendTransaction, signMessageRpc, connectAndRecover
- Entrypoint: solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}})
- Attacker controls: the Transaction/VersionedTransaction object, the connection object, options, message bytes
- Exploit idea: Find two origins sharing a 32-byte prefix and compare derived domains.
- Invariant to test: The application domain must be collision-resistant over origins.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert two distinct origins never produce the same domain from EmbeddedSolanaWalletProvider.request.
