# Q1083: timeout mapped to the same shape as success in getIsTokenUsdc.ts

## Question
The poll result mapper turns success-with-no-result into {status:'timeout'} and errors into timeouts too; can an attacker exploit that collapse through getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) so a failed deposit is presented as merely slow and the user re-sends funds?

## Target
- File/function: [src/utils/getIsTokenUsdc.ts](src/utils/getIsTokenUsdc.ts) - getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]), UsdcAddressMap, SolanaUsdcAddressMap (testnet is empty string)
- Entrypoint: funding/deposit UIs deciding USDC vs native
- Attacker controls: token address string casing, chain object, cluster name
- Exploit idea: Force error and timeout paths and compare what the caller sees.
- Invariant to test: Failure and timeout must be distinguishable to the caller.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: force each path in getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) and assert distinct result shapes.
