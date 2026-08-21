# Q1262: typed data primaryType coerced with String() in EmbeddedWalletProvider.ts

## Question
toWalletApiTypedData sets primary_type via String(typedData.primaryType) and passes types/domain/message straight through; can an attacker supply a primaryType object whose toString names a different struct so EmbeddedWalletProvider.request signs a payload with a mismatched type?

## Target
- File/function: [src/embedded/EmbeddedWalletProvider.ts](src/embedded/EmbeddedWalletProvider.ts) - EmbeddedWalletProvider.request, ensureChainId, internalSwitchEthereumChain, handleSignTransaction, handleSendTransaction, handlePopulateTransaction, handleEstimateGas, handleSwitchEthereumChain, handleIFrameRpc, handleJsonRpc
- Entrypoint: const p = await privy.embeddedWallet.getEthereumProvider(...); p.request({method, params})
- Attacker controls: method name, params[0] transaction/typed-data/message, chainId field, authorizationList
- Exploit idea: Pass an object with a custom toString as primaryType.
- Invariant to test: The primary type must be a validated key of the supplied types map.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass a non-string primaryType to EmbeddedWalletProvider.request and assert rejection.
