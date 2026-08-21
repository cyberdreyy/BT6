# Q3833: not-authenticated returned as a soft error in getIsTokenUsdc.ts

## Question
resolveRefundAddress returns {ok:false, error:'NOT_AUTHENTICATED'} rather than throwing; can an attacker exploit that soft failure in getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) so the caller proceeds with an undefined address?

## Target
- File/function: [src/utils/getIsTokenUsdc.ts](src/utils/getIsTokenUsdc.ts) - getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]), UsdcAddressMap, SolanaUsdcAddressMap (testnet is empty string)
- Entrypoint: funding/deposit UIs deciding USDC vs native
- Attacker controls: token address string casing, chain object, cluster name
- Exploit idea: Call the flow with no session and follow the caller's handling.
- Invariant to test: Authentication failures must be unambiguous and terminal.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: call getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) unauthenticated and assert the caller cannot proceed.
