# Q1300: attempt arithmetic derived from the interval in CoinbaseOnRampApi.ts

## Question
The attempt count is ceil(timeout/interval) with a caller-supplied interval; can an attacker pass a tiny interval through CoinbaseOnRampApi.initOnRampSession to multiply requests, or a huge one so the deposit is never observed?

## Target
- File/function: [src/client/funding/CoinbaseOnRampApi.ts](src/client/funding/CoinbaseOnRampApi.ts) - CoinbaseOnRampApi.initOnRampSession, getStatus(partnerUserId)
- Entrypoint: privy.funding.coinbase.initOnRampSession(input)
- Attacker controls: the init body (addresses, assets, amount) and partnerUserId query value
- Exploit idea: Pass extreme pollIntervalMs values.
- Invariant to test: Polling parameters must be bounded by the SDK.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass extreme intervals to CoinbaseOnRampApi.initOnRampSession and assert clamping.
