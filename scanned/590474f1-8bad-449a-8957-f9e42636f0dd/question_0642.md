# Q0642: source and destination currency unchecked in moonpay.ts

## Question
The quote body accepts source_currency and destination_currency as opaque strings; can an attacker submit a pair through isSupportedChainIdForMoonpay that the client never validates against getConfig, so the user approves a route they did not intend?

## Target
- File/function: [src/funding/moonpay.ts](src/funding/moonpay.ts) - isSupportedChainIdForMoonpay, chainToMoonpayCurrency (defaults to ETH_ETHEREUM on unknown chain), fundingMethodToMoonpayPaymentMethod
- Entrypoint: MoonPay funding parameter construction
- Attacker controls: chainId and asset arguments
- Exploit idea: Submit currencies absent from the deposit config.
- Invariant to test: Quote parameters must be validated against the fetched deposit configuration.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit unsupported currencies to isSupportedChainIdForMoonpay and assert client-side validation.
