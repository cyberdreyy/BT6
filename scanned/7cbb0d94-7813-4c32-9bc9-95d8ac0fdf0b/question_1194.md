# Q1194: poll swallows every operation error in getSolanaRpcEndpointForCluster.ts

## Question
poll catches all errors, records the last one and keeps iterating; can an attacker cause repeated authorization failures inside getSolanaRpcEndpointForCluster({name to be hidden until max_attempts, so the app keeps polling with a stale session?

## Target
- File/function: [src/solana/getSolanaRpcEndpointForCluster.ts](src/solana/getSolanaRpcEndpointForCluster.ts) - getSolanaRpcEndpointForCluster({name, rpcUrl}) - caller rpcUrl wins over the cluster default
- Entrypoint: SolanaClient construction for balance and mint reads
- Attacker controls: the cluster object passed in by the caller
- Exploit idea: Return 401s from the polled route and observe the loop behaviour.
- Invariant to test: Authorization failures must terminate polling immediately.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return 401 from getSolanaRpcEndpointForCluster({name's operation and assert immediate termination.
