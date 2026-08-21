# Q0386: broadcast through a caller-influenced RPC in isVersionedTransaction.ts

## Question
handleSendTransaction broadcasts with eth_sendRawTransaction through the viem client built by getJsonRpcEndpointFromChain, which prefers rpcUrls.privyWalletOverride then the supportedChains config; can an attacker supply a chain entry through Solana provider transaction handling so the signed transaction is sent to an endpoint they control?

## Target
- File/function: [src/solana/isVersionedTransaction.ts](src/solana/isVersionedTransaction.ts) - isVersionedTransaction ('version' in tx)
- Entrypoint: Solana provider transaction handling
- Attacker controls: any object shaped to satisfy or defeat the 'version' in tx check
- Exploit idea: Construct the client with a supportedChains entry carrying an override RPC and observe the broadcast target.
- Invariant to test: Broadcast endpoints must come from a trusted, pinned configuration.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a chain with a crafted override to isVersionedTransaction ('version' in tx) and assert the broadcast target is the trusted endpoint.
