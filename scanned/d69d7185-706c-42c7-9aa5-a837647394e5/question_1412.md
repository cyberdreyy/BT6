# Q1412: abort signal supplied by the caller in moonpay.ts

## Question
poll checks a caller-supplied AbortSignal; can an attacker abort isSupportedChainIdForMoonpay at a chosen moment so the app treats a completed deposit as aborted and issues a duplicate?

## Target
- File/function: [src/funding/moonpay.ts](src/funding/moonpay.ts) - isSupportedChainIdForMoonpay, chainToMoonpayCurrency (defaults to ETH_ETHEREUM on unknown chain), fundingMethodToMoonpayPaymentMethod
- Entrypoint: MoonPay funding parameter construction
- Attacker controls: chainId and asset arguments
- Exploit idea: Abort right after the funds land.
- Invariant to test: Abort must not change the recorded outcome of a settled deposit.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Integration test: abort isSupportedChainIdForMoonpay after settlement and assert the state reflects settlement.
