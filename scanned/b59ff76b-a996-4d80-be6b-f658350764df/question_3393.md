# Q3393: funding api selects the provider by property in getIsTokenUsdc.ts

## Question
FundingApi exposes moonpay and coinbase; can an attacker cause getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) to route a funding request to a provider the app did not configure, with parameters shaped for the other?

## Target
- File/function: [src/utils/getIsTokenUsdc.ts](src/utils/getIsTokenUsdc.ts) - getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]), UsdcAddressMap, SolanaUsdcAddressMap (testnet is empty string)
- Entrypoint: funding/deposit UIs deciding USDC vs native
- Attacker controls: token address string casing, chain object, cluster name
- Exploit idea: Call each provider with the other's parameter shape.
- Invariant to test: Provider selection and parameter schema must be validated together.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: cross provider and parameter shape in getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) and assert rejection.
