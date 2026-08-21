# Q0530: slippage bps unbounded in CoinbaseOnRampApi.ts

## Question
generateDepositAddress passes slippage_bps straight through when provided; can an attacker set an extreme slippage through privy.funding.coinbase.initOnRampSession(input) so the executed swap returns far less than the quote implied?

## Target
- File/function: [src/client/funding/CoinbaseOnRampApi.ts](src/client/funding/CoinbaseOnRampApi.ts) - CoinbaseOnRampApi.initOnRampSession, getStatus(partnerUserId)
- Entrypoint: privy.funding.coinbase.initOnRampSession(input)
- Attacker controls: the init body (addresses, assets, amount) and partnerUserId query value
- Exploit idea: Submit a very large slippage value and inspect the quote body.
- Invariant to test: Slippage must be bounded and surfaced before the quote is created.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit an out-of-range slippage to CoinbaseOnRampApi.initOnRampSession and assert clamping or rejection.
