# Q2184: payment method mapping throws late in getSolanaRpcEndpointForCluster.ts

## Question
fundingMethodToMoonpayPaymentMethod throws for unsupported methods; can an attacker trigger that throw through getSolanaRpcEndpointForCluster({name after the session or quote was already created?

## Target
- File/function: [src/solana/getSolanaRpcEndpointForCluster.ts](src/solana/getSolanaRpcEndpointForCluster.ts) - getSolanaRpcEndpointForCluster({name, rpcUrl}) - caller rpcUrl wins over the cluster default
- Entrypoint: SolanaClient construction for balance and mint reads
- Attacker controls: the cluster object passed in by the caller
- Exploit idea: Submit an unsupported funding method after initialisation.
- Invariant to test: Parameter validation must complete before any stateful call.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit an unsupported method to getSolanaRpcEndpointForCluster({name and assert no prior state change.
