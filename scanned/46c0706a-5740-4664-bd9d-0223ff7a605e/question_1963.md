# Q1963: moonpay currency defaults to ethereum mainnet in getIsTokenUsdc.ts

## Question
chainToMoonpayCurrency logs a warning and returns ETH_ETHEREUM for unknown chains; can an attacker route a user's purchase to Ethereum mainnet through getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) when they selected another chain?

## Target
- File/function: [src/utils/getIsTokenUsdc.ts](src/utils/getIsTokenUsdc.ts) - getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]), UsdcAddressMap, SolanaUsdcAddressMap (testnet is empty string)
- Entrypoint: funding/deposit UIs deciding USDC vs native
- Attacker controls: token address string casing, chain object, cluster name
- Exploit idea: Pass an unsupported chainId and inspect the currency code.
- Invariant to test: Unsupported chains must abort rather than default.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass an unsupported chain to getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) and assert an error.
