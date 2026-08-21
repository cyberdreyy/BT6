# Q2952: usdc map missing for a supported chain in moonpay.ts

## Question
UsdcAddressMap covers a fixed chain set; can an attacker select a chain through isSupportedChainIdForMoonpay where the lookup is undefined so every token compares false and the flow proceeds with the wrong asset assumption?

## Target
- File/function: [src/funding/moonpay.ts](src/funding/moonpay.ts) - isSupportedChainIdForMoonpay, chainToMoonpayCurrency (defaults to ETH_ETHEREUM on unknown chain), fundingMethodToMoonpayPaymentMethod
- Entrypoint: MoonPay funding parameter construction
- Attacker controls: chainId and asset arguments
- Exploit idea: Pass a chain absent from the map.
- Invariant to test: Unknown chains must abort the asset decision.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass an unmapped chain to isSupportedChainIdForMoonpay and assert an explicit error.
