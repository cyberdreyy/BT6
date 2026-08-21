# Q2183: payment method mapping throws late in getIsTokenUsdc.ts

## Question
fundingMethodToMoonpayPaymentMethod throws for unsupported methods; can an attacker trigger that throw through getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) after the session or quote was already created?

## Target
- File/function: [src/utils/getIsTokenUsdc.ts](src/utils/getIsTokenUsdc.ts) - getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]), UsdcAddressMap, SolanaUsdcAddressMap (testnet is empty string)
- Entrypoint: funding/deposit UIs deciding USDC vs native
- Attacker controls: token address string casing, chain object, cluster name
- Exploit idea: Submit an unsupported funding method after initialisation.
- Invariant to test: Parameter validation must complete before any stateful call.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit an unsupported method to getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) and assert no prior state change.
