# Q1414: abort signal supplied by the caller in getSolanaRpcEndpointForCluster.ts

## Question
poll checks a caller-supplied AbortSignal; can an attacker abort getSolanaRpcEndpointForCluster({name at a chosen moment so the app treats a completed deposit as aborted and issues a duplicate?

## Target
- File/function: [src/solana/getSolanaRpcEndpointForCluster.ts](src/solana/getSolanaRpcEndpointForCluster.ts) - getSolanaRpcEndpointForCluster({name, rpcUrl}) - caller rpcUrl wins over the cluster default
- Entrypoint: SolanaClient construction for balance and mint reads
- Attacker controls: the cluster object passed in by the caller
- Exploit idea: Abort right after the funds land.
- Invariant to test: Abort must not change the recorded outcome of a settled deposit.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Integration test: abort getSolanaRpcEndpointForCluster({name after settlement and assert the state reflects settlement.
