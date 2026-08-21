# Q1633: amount formatting patches leading dots in getIsTokenUsdc.ts

## Question
The amount helper rewrites a leading '.' to '0.' and otherwise passes the string through; can an attacker pass an amount through getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) (exponential, thousands separators, trailing characters) that the on-ramp parses differently than the app displayed?

## Target
- File/function: [src/utils/getIsTokenUsdc.ts](src/utils/getIsTokenUsdc.ts) - getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]), UsdcAddressMap, SolanaUsdcAddressMap (testnet is empty string)
- Entrypoint: funding/deposit UIs deciding USDC vs native
- Attacker controls: token address string casing, chain object, cluster name
- Exploit idea: Pass '1e3', '1,000' and '1.0abc' and inspect the URL value.
- Invariant to test: Amounts must be canonicalised and validated before they leave the SDK.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: table-test amount strings through getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]).
