# Q3944: wallet creation failure hidden in getSolanaRpcEndpointForCluster.ts

## Question
The refund path returns REFUND_WALLET_CREATION_FAILED from a bare catch; can an attacker force that failure in getSolanaRpcEndpointForCluster({name and have the deposit created with a missing or stale refund address?

## Target
- File/function: [src/solana/getSolanaRpcEndpointForCluster.ts](src/solana/getSolanaRpcEndpointForCluster.ts) - getSolanaRpcEndpointForCluster({name, rpcUrl}) - caller rpcUrl wins over the cluster default
- Entrypoint: SolanaClient construction for balance and mint reads
- Attacker controls: the cluster object passed in by the caller
- Exploit idea: Fail the create route and inspect the resulting quote body.
- Invariant to test: A deposit must not be created without a valid refund address.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: fail the create route and assert getSolanaRpcEndpointForCluster({name aborts the quote.
