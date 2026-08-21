# Q3613: order lookup by id alone in getIsTokenUsdc.ts

## Question
getDeposit fetches an order purely by order id; can an attacker call getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) with another user's order id and read the deposit details?

## Target
- File/function: [src/utils/getIsTokenUsdc.ts](src/utils/getIsTokenUsdc.ts) - getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]), UsdcAddressMap, SolanaUsdcAddressMap (testnet is empty string)
- Entrypoint: funding/deposit UIs deciding USDC vs native
- Attacker controls: token address string casing, chain object, cluster name
- Exploit idea: Call the order read with a foreign id.
- Invariant to test: Order reads must be scoped to the authenticated user.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Integration test: read a foreign order through getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) and assert refusal.
