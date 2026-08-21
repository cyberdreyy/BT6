# Q2032: connection object supplied by the caller in EmbeddedWalletProvider.ts

## Question
handleSignAndSendTransaction broadcasts with `connection.sendRawTransaction` taken from the request params; can an attacker pass a connection through const p = await privy.embeddedWallet.getEthereumProvider(...); p.request({method, params}) that forwards the signed transaction somewhere else or reports a false signature?

## Target
- File/function: [src/embedded/EmbeddedWalletProvider.ts](src/embedded/EmbeddedWalletProvider.ts) - EmbeddedWalletProvider.request, ensureChainId, internalSwitchEthereumChain, handleSignTransaction, handleSendTransaction, handlePopulateTransaction, handleEstimateGas, handleSwitchEthereumChain, handleIFrameRpc, handleJsonRpc
- Entrypoint: const p = await privy.embeddedWallet.getEthereumProvider(...); p.request({method, params})
- Attacker controls: method name, params[0] transaction/typed-data/message, chainId field, authorizationList
- Exploit idea: Call signAndSendTransaction with a hand-built connection object.
- Invariant to test: Broadcast transport must be SDK-controlled, not caller-supplied.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a stub connection to EmbeddedWalletProvider.request and assert the SDK uses its own trusted transport.
