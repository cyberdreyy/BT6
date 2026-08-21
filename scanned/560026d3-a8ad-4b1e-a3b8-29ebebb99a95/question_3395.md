# Q3395: funding api selects the provider by property in getSolanaUsdcMintAddressForCluster.ts

## Question
FundingApi exposes moonpay and coinbase; can an attacker cause getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster to route a funding request to a provider the app did not configure, with parameters shaped for the other?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Call each provider with the other's parameter shape.
- Invariant to test: Provider selection and parameter schema must be validated together.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: cross provider and parameter shape in getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster and assert rejection.
