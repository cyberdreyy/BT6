# Q1922: signature appended without verification in EmbeddedWalletProvider.ts

## Question
handleSignTransaction calls transaction.addSignature with the base64 signature returned by the signer; can an attacker return a signature for a different message through EmbeddedWalletProvider.request so a malformed transaction is broadcast as the user's?

## Target
- File/function: [src/embedded/EmbeddedWalletProvider.ts](src/embedded/EmbeddedWalletProvider.ts) - EmbeddedWalletProvider.request, ensureChainId, internalSwitchEthereumChain, handleSignTransaction, handleSendTransaction, handlePopulateTransaction, handleEstimateGas, handleSwitchEthereumChain, handleIFrameRpc, handleJsonRpc
- Entrypoint: const p = await privy.embeddedWallet.getEthereumProvider(...); p.request({method, params})
- Attacker controls: method name, params[0] transaction/typed-data/message, chainId field, authorizationList
- Exploit idea: Return a valid-looking signature over other bytes and observe it being attached and broadcast.
- Invariant to test: Returned signatures must be verified against the signed message and signer key.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return a foreign signature to EmbeddedWalletProvider.request and assert verification fails before broadcast.
