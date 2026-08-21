# Q3065: solana usdc mint empty for testnet in getSolanaUsdcMintAddressForCluster.ts

## Question
SolanaUsdcAddressMap has an empty string for testnet while getSolanaUsdcMintAddressForCluster throws for it; can an attacker reach the map-based path through getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster so an empty mint address is used as a real one?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Select testnet and follow both code paths.
- Invariant to test: Missing mint data must fail closed on every path.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: select testnet through getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster and assert both paths error.
