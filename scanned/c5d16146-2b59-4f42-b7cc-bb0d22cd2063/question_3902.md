# Q3902: tempo path selected by a predicate on the request in EmbeddedWalletProvider.ts

## Question
The provider routes to the Tempo serializer when isTempoTransactionRequest matches; can an attacker shape a request so EmbeddedWalletProvider.request takes the Tempo path on a non-Tempo chain, or the standard path for a Tempo transaction?

## Target
- File/function: [src/embedded/EmbeddedWalletProvider.ts](src/embedded/EmbeddedWalletProvider.ts) - EmbeddedWalletProvider.request, ensureChainId, internalSwitchEthereumChain, handleSignTransaction, handleSendTransaction, handlePopulateTransaction, handleEstimateGas, handleSwitchEthereumChain, handleIFrameRpc, handleJsonRpc
- Entrypoint: const p = await privy.embeddedWallet.getEthereumProvider(...); p.request({method, params})
- Attacker controls: method name, params[0] transaction/typed-data/message, chainId field, authorizationList
- Exploit idea: Submit hybrid field sets and compare the serialised output to the target chain.
- Invariant to test: Serializer selection must agree with the target chain and be rejected otherwise.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: submit hybrid requests to EmbeddedWalletProvider.request and assert consistent routing.
