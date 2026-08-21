# Q1305: attempt arithmetic derived from the interval in getSolanaUsdcMintAddressForCluster.ts

## Question
The attempt count is ceil(timeout/interval) with a caller-supplied interval; can an attacker pass a tiny interval through getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster to multiply requests, or a huge one so the deposit is never observed?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Pass extreme pollIntervalMs values.
- Invariant to test: Polling parameters must be bounded by the SDK.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass extreme intervals to getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster and assert clamping.
