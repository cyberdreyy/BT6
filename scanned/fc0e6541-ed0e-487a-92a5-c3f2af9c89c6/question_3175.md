# Q3175: cluster rpc url overrides the default in getSolanaUsdcMintAddressForCluster.ts

## Question
getSolanaRpcEndpointForCluster returns the caller's rpcUrl when set; can an attacker supply a cluster through getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster so balance and mint checks are answered by an endpoint they control and the user funds the wrong account?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Pass a cluster with a crafted rpcUrl and observe the reads driving the funding decision.
- Invariant to test: Value-bearing reads must use pinned endpoints.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass a crafted cluster to getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster and assert the pinned endpoint is used.
