# Q1923: signature appended without verification in EmbeddedSolanaWalletProvider.ts

## Question
handleSignTransaction calls transaction.addSignature with the base64 signature returned by the signer; can an attacker return a signature for a different message through EmbeddedSolanaWalletProvider.request so a malformed transaction is broadcast as the user's?

## Target
- File/function: [src/embedded/EmbeddedSolanaWalletProvider.ts](src/embedded/EmbeddedSolanaWalletProvider.ts) - EmbeddedSolanaWalletProvider.request, handleSignTransaction, handleSignAndSendTransaction, signMessageRpc, connectAndRecover
- Entrypoint: solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}})
- Attacker controls: the Transaction/VersionedTransaction object, the connection object, options, message bytes
- Exploit idea: Return a valid-looking signature over other bytes and observe it being attached and broadcast.
- Invariant to test: Returned signatures must be verified against the signed message and signer key.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return a foreign signature to EmbeddedSolanaWalletProvider.request and assert verification fails before broadcast.
