# Q0532: slippage bps unbounded in moonpay.ts

## Question
generateDepositAddress passes slippage_bps straight through when provided; can an attacker set an extreme slippage through MoonPay funding parameter construction so the executed swap returns far less than the quote implied?

## Target
- File/function: [src/funding/moonpay.ts](src/funding/moonpay.ts) - isSupportedChainIdForMoonpay, chainToMoonpayCurrency (defaults to ETH_ETHEREUM on unknown chain), fundingMethodToMoonpayPaymentMethod
- Entrypoint: MoonPay funding parameter construction
- Attacker controls: chainId and asset arguments
- Exploit idea: Submit a very large slippage value and inspect the quote body.
- Invariant to test: Slippage must be bounded and surfaced before the quote is created.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit an out-of-range slippage to isSupportedChainIdForMoonpay and assert clamping or rejection.
