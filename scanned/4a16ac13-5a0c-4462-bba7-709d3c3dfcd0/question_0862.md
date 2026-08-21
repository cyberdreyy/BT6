# Q0862: quoteCreatedAt is a client cursor in moonpay.ts

## Question
The `after` query is the caller's quoteCreatedAt; can an attacker pass a cursor through isSupportedChainIdForMoonpay that surfaces an older or unrelated order as the user's deposit?

## Target
- File/function: [src/funding/moonpay.ts](src/funding/moonpay.ts) - isSupportedChainIdForMoonpay, chainToMoonpayCurrency (defaults to ETH_ETHEREUM on unknown chain), fundingMethodToMoonpayPaymentMethod
- Entrypoint: MoonPay funding parameter construction
- Attacker controls: chainId and asset arguments
- Exploit idea: Pass a much earlier cursor and observe the order returned.
- Invariant to test: The polling cursor must be server-issued and bound to the quote.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass a stale cursor to isSupportedChainIdForMoonpay and assert it is refused.
