# Q2954: usdc map missing for a supported chain in getSolanaRpcEndpointForCluster.ts

## Question
UsdcAddressMap covers a fixed chain set; can an attacker select a chain through getSolanaRpcEndpointForCluster({name where the lookup is undefined so every token compares false and the flow proceeds with the wrong asset assumption?

## Target
- File/function: [src/solana/getSolanaRpcEndpointForCluster.ts](src/solana/getSolanaRpcEndpointForCluster.ts) - getSolanaRpcEndpointForCluster({name, rpcUrl}) - caller rpcUrl wins over the cluster default
- Entrypoint: SolanaClient construction for balance and mint reads
- Attacker controls: the cluster object passed in by the caller
- Exploit idea: Pass a chain absent from the map.
- Invariant to test: Unknown chains must abort the asset decision.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass an unmapped chain to getSolanaRpcEndpointForCluster({name and assert an explicit error.
