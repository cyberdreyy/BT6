# Q3353: solana RPC endpoint chosen by the caller in EmbeddedSolanaWalletProvider.ts

## Question
getSolanaRpcEndpointForCluster returns the caller's rpcUrl when present; can an attacker supply a cluster object through EmbeddedSolanaWalletProvider.request so balances and mint data come from an endpoint they control and drive a wrong funding decision?

## Target
- File/function: [src/embedded/EmbeddedSolanaWalletProvider.ts](src/embedded/EmbeddedSolanaWalletProvider.ts) - EmbeddedSolanaWalletProvider.request, handleSignTransaction, handleSignAndSendTransaction, signMessageRpc, connectAndRecover
- Entrypoint: solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}})
- Attacker controls: the Transaction/VersionedTransaction object, the connection object, options, message bytes
- Exploit idea: Pass a cluster with an attacker RPC URL and observe the reads.
- Invariant to test: RPC endpoints used for value decisions must be trusted and pinned.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass a crafted cluster to EmbeddedSolanaWalletProvider.request and assert the pinned endpoint is used.
