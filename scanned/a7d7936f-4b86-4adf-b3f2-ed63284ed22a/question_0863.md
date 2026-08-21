# Q0863: quoteCreatedAt is a client cursor in getIsTokenUsdc.ts

## Question
The `after` query is the caller's quoteCreatedAt; can an attacker pass a cursor through getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) that surfaces an older or unrelated order as the user's deposit?

## Target
- File/function: [src/utils/getIsTokenUsdc.ts](src/utils/getIsTokenUsdc.ts) - getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]), UsdcAddressMap, SolanaUsdcAddressMap (testnet is empty string)
- Entrypoint: funding/deposit UIs deciding USDC vs native
- Attacker controls: token address string casing, chain object, cluster name
- Exploit idea: Pass a much earlier cursor and observe the order returned.
- Invariant to test: The polling cursor must be server-issued and bound to the quote.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass a stale cursor to getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) and assert it is refused.
