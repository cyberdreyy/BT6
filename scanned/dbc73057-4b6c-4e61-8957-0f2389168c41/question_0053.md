# Q0053: chain silently switched by tx.chainId in EmbeddedSolanaWalletProvider.ts

## Question
EmbeddedWalletProvider.ensureChainId calls internalSwitchEthereumChain with the chainId found in the request params; can an unprivileged attacker submit a transaction through solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}}) whose chainId silently repoints the provider and the RPC client to another chain before signing?

## Target
- File/function: [src/embedded/EmbeddedSolanaWalletProvider.ts](src/embedded/EmbeddedSolanaWalletProvider.ts) - EmbeddedSolanaWalletProvider.request, handleSignTransaction, handleSignAndSendTransaction, signMessageRpc, connectAndRecover
- Entrypoint: solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}})
- Attacker controls: the Transaction/VersionedTransaction object, the connection object, options, message bytes
- Exploit idea: Send eth_sendTransaction with a chainId the user never selected and observe chainChanged plus the new client.
- Invariant to test: The provider chain must only change through an explicit wallet_switchEthereumChain approval.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call EmbeddedSolanaWalletProvider.request with a foreign chainId and assert no silent switch and no signature.
