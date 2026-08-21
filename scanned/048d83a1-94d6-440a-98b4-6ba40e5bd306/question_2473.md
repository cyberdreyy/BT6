# Q2473: off-chain length header is two bytes in EmbeddedSolanaWalletProvider.ts

## Question
buildSolanaOffchainMessage writes the message length as two little-endian bytes and caps the total at 1232; can an attacker craft a length that disagrees with the payload so EmbeddedSolanaWalletProvider.request or its parser reads a different message body?

## Target
- File/function: [src/embedded/EmbeddedSolanaWalletProvider.ts](src/embedded/EmbeddedSolanaWalletProvider.ts) - EmbeddedSolanaWalletProvider.request, handleSignTransaction, handleSignAndSendTransaction, signMessageRpc, connectAndRecover
- Entrypoint: solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}})
- Attacker controls: the Transaction/VersionedTransaction object, the connection object, options, message bytes
- Exploit idea: Build and then parse a message whose declared length differs from the payload.
- Invariant to test: Declared length and payload must be verified equal on both build and parse.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: fuzz length/payload pairs through build and parse in EmbeddedSolanaWalletProvider.request.
