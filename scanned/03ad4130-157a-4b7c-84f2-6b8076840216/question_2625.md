# Q2625: coinbase status by partner user id in getSolanaUsdcMintAddressForCluster.ts

## Question
CoinbaseOnRampApi.getStatus takes a partnerUserId query value from the caller; can an attacker pass another user's partner id through getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster and read their funding status?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Call getStatus with a foreign partner id.
- Invariant to test: Status lookups must be scoped to the authenticated user.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Integration test: call getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster with a foreign id and assert refusal.
