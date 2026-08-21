# Q1483: typed data domain unchecked against the chain in EmbeddedSolanaWalletProvider.ts

## Question
The domain (chainId, verifyingContract) is forwarded verbatim; can an attacker sign typed data whose domain chainId differs from the provider chain via EmbeddedSolanaWalletProvider.request, producing a signature valid on another chain?

## Target
- File/function: [src/embedded/EmbeddedSolanaWalletProvider.ts](src/embedded/EmbeddedSolanaWalletProvider.ts) - EmbeddedSolanaWalletProvider.request, handleSignTransaction, handleSignAndSendTransaction, signMessageRpc, connectAndRecover
- Entrypoint: solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}})
- Attacker controls: the Transaction/VersionedTransaction object, the connection object, options, message bytes
- Exploit idea: Submit typed data with a foreign domain.chainId while the provider is on mainnet.
- Invariant to test: The typed-data domain must agree with the provider's active chain.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a mismatched domain chainId to EmbeddedSolanaWalletProvider.request and assert rejection.
