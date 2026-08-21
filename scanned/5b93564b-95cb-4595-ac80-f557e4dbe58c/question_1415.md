# Q1415: abort signal supplied by the caller in getSolanaUsdcMintAddressForCluster.ts

## Question
poll checks a caller-supplied AbortSignal; can an attacker abort getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster at a chosen moment so the app treats a completed deposit as aborted and issues a duplicate?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Abort right after the funds land.
- Invariant to test: Abort must not change the recorded outcome of a settled deposit.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Integration test: abort getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster after settlement and assert the state reflects settlement.
