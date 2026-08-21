# Q3243: disconnect leaves the wrapper usable in EmbeddedSolanaWalletProvider.ts

## Question
disconnect only calls the standard feature; can an attacker keep using EmbeddedSolanaWalletProvider.request after disconnect so signatures are still requested from a wallet the user disconnected?

## Target
- File/function: [src/embedded/EmbeddedSolanaWalletProvider.ts](src/embedded/EmbeddedSolanaWalletProvider.ts) - EmbeddedSolanaWalletProvider.request, handleSignTransaction, handleSignAndSendTransaction, signMessageRpc, connectAndRecover
- Entrypoint: solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}})
- Attacker controls: the Transaction/VersionedTransaction object, the connection object, options, message bytes
- Exploit idea: Call disconnect then sign.
- Invariant to test: A disconnected wallet wrapper must refuse further operations.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call disconnect then sign through EmbeddedSolanaWalletProvider.request and assert rejection.
