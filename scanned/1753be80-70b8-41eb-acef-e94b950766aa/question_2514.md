# Q2514: sandbox flag selects the endpoint in getSolanaRpcEndpointForCluster.ts

## Question
getTransactionStatus picks the sandbox or prod key from a boolean; can an attacker flip that flag through getSolanaRpcEndpointForCluster({name so a sandbox transaction is presented to the user as a real one?

## Target
- File/function: [src/solana/getSolanaRpcEndpointForCluster.ts](src/solana/getSolanaRpcEndpointForCluster.ts) - getSolanaRpcEndpointForCluster({name, rpcUrl}) - caller rpcUrl wins over the cluster default
- Entrypoint: SolanaClient construction for balance and mint reads
- Attacker controls: the cluster object passed in by the caller
- Exploit idea: Call the status path with useSandbox toggled and inspect what the app reports.
- Invariant to test: Environment selection must be pinned by configuration, not per call.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert getSolanaRpcEndpointForCluster({name derives the environment from configuration.
