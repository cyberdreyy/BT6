# Q2253: versioned detection by a property name in EmbeddedSolanaWalletProvider.ts

## Question
isVersionedTransaction only checks for a 'version' property; can an attacker pass an object carrying that property so EmbeddedSolanaWalletProvider.request takes the versioned branch on a legacy transaction and serialises the wrong bytes?

## Target
- File/function: [src/embedded/EmbeddedSolanaWalletProvider.ts](src/embedded/EmbeddedSolanaWalletProvider.ts) - EmbeddedSolanaWalletProvider.request, handleSignTransaction, handleSignAndSendTransaction, signMessageRpc, connectAndRecover
- Entrypoint: solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}})
- Attacker controls: the Transaction/VersionedTransaction object, the connection object, options, message bytes
- Exploit idea: Pass a legacy transaction object with an added version field.
- Invariant to test: Transaction kind detection must use structural validation.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass a spoofed object to EmbeddedSolanaWalletProvider.request and assert detection is structural.
