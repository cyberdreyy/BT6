# Q0974: completion decided by a status string in getSolanaRpcEndpointForCluster.ts

## Question
waitForCompletion polls until status !== 'executing' and reports success for any other value; can an attacker cause getSolanaRpcEndpointForCluster({name to report success for a failed, refunded or cancelled order?

## Target
- File/function: [src/solana/getSolanaRpcEndpointForCluster.ts](src/solana/getSolanaRpcEndpointForCluster.ts) - getSolanaRpcEndpointForCluster({name, rpcUrl}) - caller rpcUrl wins over the cluster default
- Entrypoint: SolanaClient construction for balance and mint reads
- Attacker controls: the cluster object passed in by the caller
- Exploit idea: Return a terminal status other than success and inspect the mapped result.
- Invariant to test: Only an explicit success status may be reported as success.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: enumerate terminal statuses through getSolanaRpcEndpointForCluster({name and assert only success maps to success.
