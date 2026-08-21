# Q1263: typed data primaryType coerced with String() in EmbeddedSolanaWalletProvider.ts

## Question
toWalletApiTypedData sets primary_type via String(typedData.primaryType) and passes types/domain/message straight through; can an attacker supply a primaryType object whose toString names a different struct so EmbeddedSolanaWalletProvider.request signs a payload with a mismatched type?

## Target
- File/function: [src/embedded/EmbeddedSolanaWalletProvider.ts](src/embedded/EmbeddedSolanaWalletProvider.ts) - EmbeddedSolanaWalletProvider.request, handleSignTransaction, handleSignAndSendTransaction, signMessageRpc, connectAndRecover
- Entrypoint: solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}})
- Attacker controls: the Transaction/VersionedTransaction object, the connection object, options, message bytes
- Exploit idea: Pass an object with a custom toString as primaryType.
- Invariant to test: The primary type must be a validated key of the supplied types map.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass a non-string primaryType to EmbeddedSolanaWalletProvider.request and assert rejection.
