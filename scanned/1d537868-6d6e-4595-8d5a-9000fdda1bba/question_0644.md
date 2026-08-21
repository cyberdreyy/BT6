# Q0644: source and destination currency unchecked in getSolanaRpcEndpointForCluster.ts

## Question
The quote body accepts source_currency and destination_currency as opaque strings; can an attacker submit a pair through getSolanaRpcEndpointForCluster({name that the client never validates against getConfig, so the user approves a route they did not intend?

## Target
- File/function: [src/solana/getSolanaRpcEndpointForCluster.ts](src/solana/getSolanaRpcEndpointForCluster.ts) - getSolanaRpcEndpointForCluster({name, rpcUrl}) - caller rpcUrl wins over the cluster default
- Entrypoint: SolanaClient construction for balance and mint reads
- Attacker controls: the cluster object passed in by the caller
- Exploit idea: Submit currencies absent from the deposit config.
- Invariant to test: Quote parameters must be validated against the fetched deposit configuration.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit unsupported currencies to getSolanaRpcEndpointForCluster({name and assert client-side validation.
