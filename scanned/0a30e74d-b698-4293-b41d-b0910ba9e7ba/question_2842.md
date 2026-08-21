# Q2842: usdc detection by exact address equality in moonpay.ts

## Question
getIsTokenUsdc compares the supplied address to UsdcAddressMap[chain.id] with ===; can an attacker pass a checksummed or padded variant through isSupportedChainIdForMoonpay so a genuine USDC transfer is classified as an unknown token (or a lookalike is treated as USDC)?

## Target
- File/function: [src/funding/moonpay.ts](src/funding/moonpay.ts) - isSupportedChainIdForMoonpay, chainToMoonpayCurrency (defaults to ETH_ETHEREUM on unknown chain), fundingMethodToMoonpayPaymentMethod
- Entrypoint: MoonPay funding parameter construction
- Attacker controls: chainId and asset arguments
- Exploit idea: Pass mixed-case and zero-padded variants of the USDC address.
- Invariant to test: Token identity comparison must be canonical.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: table-test address forms through isSupportedChainIdForMoonpay.
