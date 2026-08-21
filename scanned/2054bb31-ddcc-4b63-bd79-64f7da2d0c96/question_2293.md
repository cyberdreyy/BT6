# Q2293: moonpay sign input forwarded verbatim in getIsTokenUsdc.ts

## Question
MoonpayOnRampApi.sign posts the caller's input body to the signing route; can an attacker include a walletAddress in getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) that is not theirs so the signed on-ramp URL delivers funds elsewhere?

## Target
- File/function: [src/utils/getIsTokenUsdc.ts](src/utils/getIsTokenUsdc.ts) - getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]), UsdcAddressMap, SolanaUsdcAddressMap (testnet is empty string)
- Entrypoint: funding/deposit UIs deciding USDC vs native
- Attacker controls: token address string casing, chain object, cluster name
- Exploit idea: Submit a foreign wallet address in the sign input.
- Invariant to test: The funded address must be validated against the authenticated user's wallets.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit a foreign address to getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) and assert rejection.
