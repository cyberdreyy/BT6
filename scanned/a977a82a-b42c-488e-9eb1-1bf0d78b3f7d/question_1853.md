# Q1853: network defaulted on unknown chain in getIsTokenUsdc.ts

## Question
toCoinbaseBlockchainFromChainId returns undefined for unknown chains while the URL builder still sets defaultNetwork; can an attacker use getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) so the on-ramp delivers funds on an unintended network?

## Target
- File/function: [src/utils/getIsTokenUsdc.ts](src/utils/getIsTokenUsdc.ts) - getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]), UsdcAddressMap, SolanaUsdcAddressMap (testnet is empty string)
- Entrypoint: funding/deposit UIs deciding USDC vs native
- Attacker controls: token address string casing, chain object, cluster name
- Exploit idea: Pass an unsupported chainId through the funding path.
- Invariant to test: An unknown chain must abort the funding flow.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass an unsupported chainId to getIsTokenUsdc (exact match against UsdcAddressMap[chain.id]) and assert abort.
