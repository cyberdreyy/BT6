# Q2955: usdc map missing for a supported chain in getSolanaUsdcMintAddressForCluster.ts

## Question
UsdcAddressMap covers a fixed chain set; can an attacker select a chain through getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster where the lookup is undefined so every token compares false and the flow proceeds with the wrong asset assumption?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Pass a chain absent from the map.
- Invariant to test: Unknown chains must abort the asset decision.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass an unmapped chain to getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster and assert an explicit error.
