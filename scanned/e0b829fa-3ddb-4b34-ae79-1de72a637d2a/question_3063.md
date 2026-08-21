# Q3063: solana usdc mint empty for testnet in getIsTokenUsdc.ts

## Question
SolanaUsdcAddressMap has an empty string for testnet while getSolanaUsdcMintAddressForCluster throws for it; can an attacker reach the map-based path through getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) so an empty mint address is used as a real one?

## Target
- File/function: [src/utils/getIsTokenUsdc.ts](src/utils/getIsTokenUsdc.ts) - getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]), UsdcAddressMap, SolanaUsdcAddressMap (testnet is empty string)
- Entrypoint: funding/deposit UIs deciding USDC vs native
- Attacker controls: token address string casing, chain object, cluster name
- Exploit idea: Select testnet and follow both code paths.
- Invariant to test: Missing mint data must fail closed on every path.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: select testnet through getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) and assert both paths error.
