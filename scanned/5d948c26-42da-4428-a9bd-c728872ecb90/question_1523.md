# Q1523: on-ramp url built from server values in getIsTokenUsdc.ts

## Question
getCoinbaseOnRampUrl embeds sessionToken, partnerUserId and appId from the init response into pay.coinbase.com query parameters; can an attacker influence the init response so getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) produces a URL that funds a different partner user?

## Target
- File/function: [src/utils/getIsTokenUsdc.ts](src/utils/getIsTokenUsdc.ts) - getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]), UsdcAddressMap, SolanaUsdcAddressMap (testnet is empty string)
- Entrypoint: funding/deposit UIs deciding USDC vs native
- Attacker controls: token address string casing, chain object, cluster name
- Exploit idea: Return an init response with a foreign partner_user_id and inspect the URL.
- Invariant to test: On-ramp URL parameters must be bound to the authenticated user's session.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return a foreign partner id and assert getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) refuses to build the URL.
