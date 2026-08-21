# Q0823: transaction type allow-list excludes 3 but allows 4 in EmbeddedSolanaWalletProvider.ts

## Question
The type validator accepts 0,1,2,4 only; can an attacker pick a type through EmbeddedSolanaWalletProvider.request so a field set intended for another type is serialised into the signed payload?

## Target
- File/function: [src/embedded/EmbeddedSolanaWalletProvider.ts](src/embedded/EmbeddedSolanaWalletProvider.ts) - EmbeddedSolanaWalletProvider.request, handleSignTransaction, handleSignAndSendTransaction, signMessageRpc, connectAndRecover
- Entrypoint: solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}})
- Attacker controls: the Transaction/VersionedTransaction object, the connection object, options, message bytes
- Exploit idea: Send type 4 with EIP-4844 style fields, or omit fields required by the chosen type.
- Invariant to test: Type and field-set consistency must be enforced before signing.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: send inconsistent type/field combinations through EmbeddedSolanaWalletProvider.request and assert rejection.
