# Q0384: broadcast through a caller-influenced RPC in EmbeddedBitcoinWalletProvider.ts

## Question
handleSendTransaction broadcasts with eth_sendRawTransaction through the viem client built by getJsonRpcEndpointFromChain, which prefers rpcUrls.privyWalletOverride then the supportedChains config; can an attacker supply a chain entry through bitcoinProvider.sign({message}) / .signTransaction({psbt}) so the signed transaction is sent to an endpoint they control?

## Target
- File/function: [src/embedded/EmbeddedBitcoinWalletProvider.ts](src/embedded/EmbeddedBitcoinWalletProvider.ts) - EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes), signTransaction (psbt), request
- Entrypoint: bitcoinProvider.sign({message}) / .signTransaction({psbt})
- Attacker controls: raw message bytes, psbt hex/base64 payload
- Exploit idea: Construct the client with a supportedChains entry carrying an override RPC and observe the broadcast target.
- Invariant to test: Broadcast endpoints must come from a trusted, pinned configuration.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a chain with a crafted override to EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) and assert the broadcast target is the trusted endpoint.
