# Q3394: funding api selects the provider by property in getSolanaRpcEndpointForCluster.ts

## Question
FundingApi exposes moonpay and coinbase; can an attacker cause getSolanaRpcEndpointForCluster({name to route a funding request to a provider the app did not configure, with parameters shaped for the other?

## Target
- File/function: [src/solana/getSolanaRpcEndpointForCluster.ts](src/solana/getSolanaRpcEndpointForCluster.ts) - getSolanaRpcEndpointForCluster({name, rpcUrl}) - caller rpcUrl wins over the cluster default
- Entrypoint: SolanaClient construction for balance and mint reads
- Attacker controls: the cluster object passed in by the caller
- Exploit idea: Call each provider with the other's parameter shape.
- Invariant to test: Provider selection and parameter schema must be validated together.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: cross provider and parameter shape in getSolanaRpcEndpointForCluster({name and assert rejection.
