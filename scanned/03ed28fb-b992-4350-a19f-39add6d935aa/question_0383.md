# Q0383: broadcast through a caller-influenced RPC in EmbeddedSolanaWalletProvider.ts

## Question
handleSendTransaction broadcasts with eth_sendRawTransaction through the viem client built by getJsonRpcEndpointFromChain, which prefers rpcUrls.privyWalletOverride then the supportedChains config; can an attacker supply a chain entry through solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}}) so the signed transaction is sent to an endpoint they control?

## Target
- File/function: [src/embedded/EmbeddedSolanaWalletProvider.ts](src/embedded/EmbeddedSolanaWalletProvider.ts) - EmbeddedSolanaWalletProvider.request, handleSignTransaction, handleSignAndSendTransaction, signMessageRpc, connectAndRecover
- Entrypoint: solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}})
- Attacker controls: the Transaction/VersionedTransaction object, the connection object, options, message bytes
- Exploit idea: Construct the client with a supportedChains entry carrying an override RPC and observe the broadcast target.
- Invariant to test: Broadcast endpoints must come from a trusted, pinned configuration.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a chain with a crafted override to EmbeddedSolanaWalletProvider.request and assert the broadcast target is the trusted endpoint.
