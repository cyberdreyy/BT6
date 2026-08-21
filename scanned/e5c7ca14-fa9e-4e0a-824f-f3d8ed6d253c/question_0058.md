# Q0058: chain silently switched by tx.chainId in ConnectedStandardSolanaWallet.ts

## Question
EmbeddedWalletProvider.ensureChainId calls internalSwitchEthereumChain with the chainId found in the request params; can an unprivileged attacker submit a transaction through new ConnectedStandardSolanaWallet({wallet, account}) then sign* whose chainId silently repoints the provider and the RPC client to another chain before signing?

## Target
- File/function: [src/solana/ConnectedStandardSolanaWallet.ts](src/solana/ConnectedStandardSolanaWallet.ts) - ConnectedStandardSolanaWallet.signMessage, signTransaction, signAndSendTransaction, signAndSendAllTransactions, disconnect (account injected into every feature call)
- Entrypoint: new ConnectedStandardSolanaWallet({wallet, account}) then sign*
- Attacker controls: the inputs spread into the wallet-standard feature calls and the returned array shape
- Exploit idea: Send eth_sendTransaction with a chainId the user never selected and observe chainChanged plus the new client.
- Invariant to test: The provider chain must only change through an explicit wallet_switchEthereumChain approval.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call ConnectedStandardSolanaWallet.signMessage with a foreign chainId and assert no silent switch and no signature.
