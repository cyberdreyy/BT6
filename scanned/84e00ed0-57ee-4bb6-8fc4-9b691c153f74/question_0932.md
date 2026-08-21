# Q0932: data field re-encoded from arrays in EmbeddedWalletProvider.ts

## Question
The data encoder accepts a string, a Buffer or a number array and hex-encodes non-hex strings as UTF-8; can an attacker submit calldata that the encoder transforms into different bytes via EmbeddedWalletProvider.request?

## Target
- File/function: [src/embedded/EmbeddedWalletProvider.ts](src/embedded/EmbeddedWalletProvider.ts) - EmbeddedWalletProvider.request, ensureChainId, internalSwitchEthereumChain, handleSignTransaction, handleSendTransaction, handlePopulateTransaction, handleEstimateGas, handleSwitchEthereumChain, handleIFrameRpc, handleJsonRpc
- Entrypoint: const p = await privy.embeddedWallet.getEthereumProvider(...); p.request({method, params})
- Attacker controls: method name, params[0] transaction/typed-data/message, chainId field, authorizationList
- Exploit idea: Send data as '0xzz', as an array with out-of-range members, and as a UTF-8 string.
- Invariant to test: Calldata must be passed through byte-exact or rejected.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: submit each data form to EmbeddedWalletProvider.request and assert byte equality with the input.
