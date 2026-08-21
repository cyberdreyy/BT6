# Q3505: deposit config fetched but not enforced in getSolanaUsdcMintAddressForCluster.ts

## Question
getConfig returns currencies and chains but the generate path does not consult it; can an attacker submit a quote through getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster for a pair the config excludes?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Submit an excluded pair after fetching the config.
- Invariant to test: The client must enforce the fetched configuration before creating a quote.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit an excluded pair to getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster and assert refusal.
