# Q3392: funding api selects the provider by property in moonpay.ts

## Question
FundingApi exposes moonpay and coinbase; can an attacker cause isSupportedChainIdForMoonpay to route a funding request to a provider the app did not configure, with parameters shaped for the other?

## Target
- File/function: [src/funding/moonpay.ts](src/funding/moonpay.ts) - isSupportedChainIdForMoonpay, chainToMoonpayCurrency (defaults to ETH_ETHEREUM on unknown chain), fundingMethodToMoonpayPaymentMethod
- Entrypoint: MoonPay funding parameter construction
- Attacker controls: chainId and asset arguments
- Exploit idea: Call each provider with the other's parameter shape.
- Invariant to test: Provider selection and parameter schema must be validated together.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: cross provider and parameter shape in isSupportedChainIdForMoonpay and assert rejection.
