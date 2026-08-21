# Q0528: slippage bps unbounded in FundingApi.ts

## Question
generateDepositAddress passes slippage_bps straight through when provided; can an attacker set an extreme slippage through privy.funding.* so the executed swap returns far less than the quote implied?

## Target
- File/function: [src/client/funding/FundingApi.ts](src/client/funding/FundingApi.ts) - FundingApi.moonpay, FundingApi.coinbase
- Entrypoint: privy.funding.*
- Attacker controls: which on-ramp is selected and the input object forwarded to it
- Exploit idea: Submit a very large slippage value and inspect the quote body.
- Invariant to test: Slippage must be bounded and surfaced before the quote is created.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit an out-of-range slippage to FundingApi.moonpay and assert clamping or rejection.
