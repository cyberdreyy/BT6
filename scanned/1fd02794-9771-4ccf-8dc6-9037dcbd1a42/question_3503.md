# Q3503: deposit config fetched but not enforced in getIsTokenUsdc.ts

## Question
getConfig returns currencies and chains but the generate path does not consult it; can an attacker submit a quote through getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) for a pair the config excludes?

## Target
- File/function: [src/utils/getIsTokenUsdc.ts](src/utils/getIsTokenUsdc.ts) - getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]), UsdcAddressMap, SolanaUsdcAddressMap (testnet is empty string)
- Entrypoint: funding/deposit UIs deciding USDC vs native
- Attacker controls: token address string casing, chain object, cluster name
- Exploit idea: Submit an excluded pair after fetching the config.
- Invariant to test: The client must enforce the fetched configuration before creating a quote.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit an excluded pair to getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) and assert refusal.
