# Q1413: abort signal supplied by the caller in getIsTokenUsdc.ts

## Question
poll checks a caller-supplied AbortSignal; can an attacker abort getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) at a chosen moment so the app treats a completed deposit as aborted and issues a duplicate?

## Target
- File/function: [src/utils/getIsTokenUsdc.ts](src/utils/getIsTokenUsdc.ts) - getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]), UsdcAddressMap, SolanaUsdcAddressMap (testnet is empty string)
- Entrypoint: funding/deposit UIs deciding USDC vs native
- Attacker controls: token address string casing, chain object, cluster name
- Exploit idea: Abort right after the funds land.
- Invariant to test: Abort must not change the recorded outcome of a settled deposit.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Integration test: abort getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) after settlement and assert the state reflects settlement.
