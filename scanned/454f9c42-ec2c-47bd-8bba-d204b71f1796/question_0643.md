# Q0643: source and destination currency unchecked in getIsTokenUsdc.ts

## Question
The quote body accepts source_currency and destination_currency as opaque strings; can an attacker submit a pair through getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) that the client never validates against getConfig, so the user approves a route they did not intend?

## Target
- File/function: [src/utils/getIsTokenUsdc.ts](src/utils/getIsTokenUsdc.ts) - getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]), UsdcAddressMap, SolanaUsdcAddressMap (testnet is empty string)
- Entrypoint: funding/deposit UIs deciding USDC vs native
- Attacker controls: token address string casing, chain object, cluster name
- Exploit idea: Submit currencies absent from the deposit config.
- Invariant to test: Quote parameters must be validated against the fetched deposit configuration.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit unsupported currencies to getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) and assert client-side validation.
