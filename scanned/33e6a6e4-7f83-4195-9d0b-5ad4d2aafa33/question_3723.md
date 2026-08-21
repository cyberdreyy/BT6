# Q3723: onWalletCreated callback fires before confirmation in getIsTokenUsdc.ts

## Question
resolveRefundAddress invokes onWalletCreated after the create call returns; can an attacker use getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) so the app treats an unconfirmed wallet as ready and routes funds to it?

## Target
- File/function: [src/utils/getIsTokenUsdc.ts](src/utils/getIsTokenUsdc.ts) - getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]), UsdcAddressMap, SolanaUsdcAddressMap (testnet is empty string)
- Entrypoint: funding/deposit UIs deciding USDC vs native
- Attacker controls: token address string casing, chain object, cluster name
- Exploit idea: Return a create response and inspect the callback timing versus session refresh.
- Invariant to test: Callbacks signalling readiness must follow a confirmed session refresh.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) refreshes the user before invoking the callback.
