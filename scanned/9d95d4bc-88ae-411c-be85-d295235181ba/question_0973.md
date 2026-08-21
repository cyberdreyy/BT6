# Q0973: completion decided by a status string in getIsTokenUsdc.ts

## Question
waitForCompletion polls until status !== 'executing' and reports success for any other value; can an attacker cause getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) to report success for a failed, refunded or cancelled order?

## Target
- File/function: [src/utils/getIsTokenUsdc.ts](src/utils/getIsTokenUsdc.ts) - getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]), UsdcAddressMap, SolanaUsdcAddressMap (testnet is empty string)
- Entrypoint: funding/deposit UIs deciding USDC vs native
- Attacker controls: token address string casing, chain object, cluster name
- Exploit idea: Return a terminal status other than success and inspect the mapped result.
- Invariant to test: Only an explicit success status may be reported as success.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: enumerate terminal statuses through getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) and assert only success maps to success.
