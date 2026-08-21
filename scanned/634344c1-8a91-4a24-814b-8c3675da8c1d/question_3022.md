# Q3022: wallet-standard features called with an injected account in EmbeddedWalletProvider.ts

## Question
ConnectedStandardSolanaWallet spreads `{...input, account: this.#t}` into every feature call; can an attacker construct the wrapper with an account that does not match the underlying wallet so EmbeddedWalletProvider.request requests signatures for a foreign account?

## Target
- File/function: [src/embedded/EmbeddedWalletProvider.ts](src/embedded/EmbeddedWalletProvider.ts) - EmbeddedWalletProvider.request, ensureChainId, internalSwitchEthereumChain, handleSignTransaction, handleSendTransaction, handlePopulateTransaction, handleEstimateGas, handleSwitchEthereumChain, handleIFrameRpc, handleJsonRpc
- Entrypoint: const p = await privy.embeddedWallet.getEthereumProvider(...); p.request({method, params})
- Attacker controls: method name, params[0] transaction/typed-data/message, chainId field, authorizationList
- Exploit idea: Construct the wrapper with a mismatched account/wallet pair.
- Invariant to test: The wrapped account must be verified to belong to the wrapped wallet.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: construct EmbeddedWalletProvider.request with a mismatched pair and assert construction fails.
