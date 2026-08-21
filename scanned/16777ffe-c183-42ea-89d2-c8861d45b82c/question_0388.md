# Q0388: broadcast through a caller-influenced RPC in ConnectedStandardSolanaWallet.ts

## Question
handleSendTransaction broadcasts with eth_sendRawTransaction through the viem client built by getJsonRpcEndpointFromChain, which prefers rpcUrls.privyWalletOverride then the supportedChains config; can an attacker supply a chain entry through new ConnectedStandardSolanaWallet({wallet, account}) then sign* so the signed transaction is sent to an endpoint they control?

## Target
- File/function: [src/solana/ConnectedStandardSolanaWallet.ts](src/solana/ConnectedStandardSolanaWallet.ts) - ConnectedStandardSolanaWallet.signMessage, signTransaction, signAndSendTransaction, signAndSendAllTransactions, disconnect (account injected into every feature call)
- Entrypoint: new ConnectedStandardSolanaWallet({wallet, account}) then sign*
- Attacker controls: the inputs spread into the wallet-standard feature calls and the returned array shape
- Exploit idea: Construct the client with a supportedChains entry carrying an override RPC and observe the broadcast target.
- Invariant to test: Broadcast endpoints must come from a trusted, pinned configuration.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a chain with a crafted override to ConnectedStandardSolanaWallet.signMessage and assert the broadcast target is the trusted endpoint.
