# Q2953: usdc map missing for a supported chain in getIsTokenUsdc.ts

## Question
UsdcAddressMap covers a fixed chain set; can an attacker select a chain through getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) where the lookup is undefined so every token compares false and the flow proceeds with the wrong asset assumption?

## Target
- File/function: [src/utils/getIsTokenUsdc.ts](src/utils/getIsTokenUsdc.ts) - getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]), UsdcAddressMap, SolanaUsdcAddressMap (testnet is empty string)
- Entrypoint: funding/deposit UIs deciding USDC vs native
- Attacker controls: token address string casing, chain object, cluster name
- Exploit idea: Pass a chain absent from the map.
- Invariant to test: Unknown chains must abort the asset decision.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass an unmapped chain to getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) and assert an explicit error.
