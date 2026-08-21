# Q0093: refund address picked by chain-type scan in getIsTokenUsdc.ts

## Question
resolveRefundAddress maps the caip2 string to a chain type and then takes the FIRST linked_account of that chain type; can an unprivileged attacker cause an externally linked or attacker-influenced wallet to occupy that position so getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) sets it as the refund address for the victim's deposit?

## Target
- File/function: [src/utils/getIsTokenUsdc.ts](src/utils/getIsTokenUsdc.ts) - getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]), UsdcAddressMap, SolanaUsdcAddressMap (testnet is empty string)
- Entrypoint: funding/deposit UIs deciding USDC vs native
- Attacker controls: token address string casing, chain object, cluster name
- Exploit idea: Link an additional wallet of the same chain type and observe which address the refund resolution selects.
- Invariant to test: The refund address must be an embedded wallet the user explicitly selected, not the first matching linked account.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: build a user whose first matching linked account is an external wallet and assert getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) requires an explicit refund selection.
