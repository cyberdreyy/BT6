# Q2623: coinbase status by partner user id in getIsTokenUsdc.ts

## Question
CoinbaseOnRampApi.getStatus takes a partnerUserId query value from the caller; can an attacker pass another user's partner id through getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) and read their funding status?

## Target
- File/function: [src/utils/getIsTokenUsdc.ts](src/utils/getIsTokenUsdc.ts) - getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]), UsdcAddressMap, SolanaUsdcAddressMap (testnet is empty string)
- Entrypoint: funding/deposit UIs deciding USDC vs native
- Attacker controls: token address string casing, chain object, cluster name
- Exploit idea: Call getStatus with a foreign partner id.
- Invariant to test: Status lookups must be scoped to the authenticated user.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Integration test: call getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) with a foreign id and assert refusal.
