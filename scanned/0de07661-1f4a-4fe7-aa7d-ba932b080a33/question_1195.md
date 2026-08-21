# Q1195: poll swallows every operation error in getSolanaUsdcMintAddressForCluster.ts

## Question
poll catches all errors, records the last one and keeps iterating; can an attacker cause repeated authorization failures inside getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster to be hidden until max_attempts, so the app keeps polling with a stale session?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Return 401s from the polled route and observe the loop behaviour.
- Invariant to test: Authorization failures must terminate polling immediately.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return 401 from getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster's operation and assert immediate termination.
