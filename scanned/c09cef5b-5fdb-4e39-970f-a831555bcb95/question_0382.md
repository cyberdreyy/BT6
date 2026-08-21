# Q0382: broadcast through a caller-influenced RPC in EmbeddedWalletProvider.ts

## Question
handleSendTransaction broadcasts with eth_sendRawTransaction through the viem client built by getJsonRpcEndpointFromChain, which prefers rpcUrls.privyWalletOverride then the supportedChains config; can an attacker supply a chain entry through const p = await privy.embeddedWallet.getEthereumProvider(...); p.request({method, params}) so the signed transaction is sent to an endpoint they control?

## Target
- File/function: [src/embedded/EmbeddedWalletProvider.ts](src/embedded/EmbeddedWalletProvider.ts) - EmbeddedWalletProvider.request, ensureChainId, internalSwitchEthereumChain, handleSignTransaction, handleSendTransaction, handlePopulateTransaction, handleEstimateGas, handleSwitchEthereumChain, handleIFrameRpc, handleJsonRpc
- Entrypoint: const p = await privy.embeddedWallet.getEthereumProvider(...); p.request({method, params})
- Attacker controls: method name, params[0] transaction/typed-data/message, chainId field, authorizationList
- Exploit idea: Construct the client with a supportedChains entry carrying an override RPC and observe the broadcast target.
- Invariant to test: Broadcast endpoints must come from a trusted, pinned configuration.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a chain with a crafted override to EmbeddedWalletProvider.request and assert the broadcast target is the trusted endpoint.
