# Q3504: deposit config fetched but not enforced in getSolanaRpcEndpointForCluster.ts

## Question
getConfig returns currencies and chains but the generate path does not consult it; can an attacker submit a quote through getSolanaRpcEndpointForCluster({name for a pair the config excludes?

## Target
- File/function: [src/solana/getSolanaRpcEndpointForCluster.ts](src/solana/getSolanaRpcEndpointForCluster.ts) - getSolanaRpcEndpointForCluster({name, rpcUrl}) - caller rpcUrl wins over the cluster default
- Entrypoint: SolanaClient construction for balance and mint reads
- Attacker controls: the cluster object passed in by the caller
- Exploit idea: Submit an excluded pair after fetching the config.
- Invariant to test: The client must enforce the fetched configuration before creating a quote.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit an excluded pair to getSolanaRpcEndpointForCluster({name and assert refusal.
