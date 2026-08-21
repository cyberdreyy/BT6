# Q3502: deposit config fetched but not enforced in moonpay.ts

## Question
getConfig returns currencies and chains but the generate path does not consult it; can an attacker submit a quote through isSupportedChainIdForMoonpay for a pair the config excludes?

## Target
- File/function: [src/funding/moonpay.ts](src/funding/moonpay.ts) - isSupportedChainIdForMoonpay, chainToMoonpayCurrency (defaults to ETH_ETHEREUM on unknown chain), fundingMethodToMoonpayPaymentMethod
- Entrypoint: MoonPay funding parameter construction
- Attacker controls: chainId and asset arguments
- Exploit idea: Submit an excluded pair after fetching the config.
- Invariant to test: The client must enforce the fetched configuration before creating a quote.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit an excluded pair to isSupportedChainIdForMoonpay and assert refusal.
