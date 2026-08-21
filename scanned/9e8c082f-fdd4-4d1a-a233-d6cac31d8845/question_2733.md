# Q2733: init body carries the destination address in getIsTokenUsdc.ts

## Question
initOnRampSession forwards the caller's body including addresses and assets; can an attacker submit a destination through getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) that is not the user's wallet?

## Target
- File/function: [src/utils/getIsTokenUsdc.ts](src/utils/getIsTokenUsdc.ts) - getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]), UsdcAddressMap, SolanaUsdcAddressMap (testnet is empty string)
- Entrypoint: funding/deposit UIs deciding USDC vs native
- Attacker controls: token address string casing, chain object, cluster name
- Exploit idea: Submit a foreign address in the init body.
- Invariant to test: Funding destinations must be validated against the user's wallets.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit a foreign address to getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) and assert rejection.
