# Q2513: sandbox flag selects the endpoint in getIsTokenUsdc.ts

## Question
getTransactionStatus picks the sandbox or prod key from a boolean; can an attacker flip that flag through getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) so a sandbox transaction is presented to the user as a real one?

## Target
- File/function: [src/utils/getIsTokenUsdc.ts](src/utils/getIsTokenUsdc.ts) - getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]), UsdcAddressMap, SolanaUsdcAddressMap (testnet is empty string)
- Entrypoint: funding/deposit UIs deciding USDC vs native
- Attacker controls: token address string casing, chain object, cluster name
- Exploit idea: Call the status path with useSandbox toggled and inspect what the app reports.
- Invariant to test: Environment selection must be pinned by configuration, not per call.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) derives the environment from configuration.
