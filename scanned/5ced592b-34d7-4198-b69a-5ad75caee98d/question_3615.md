# Q3615: order lookup by id alone in getSolanaUsdcMintAddressForCluster.ts

## Question
getDeposit fetches an order purely by order id; can an attacker call getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster with another user's order id and read the deposit details?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Call the order read with a foreign id.
- Invariant to test: Order reads must be scoped to the authenticated user.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Integration test: read a foreign order through getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster and assert refusal.
