# Q1410: abort signal supplied by the caller in CoinbaseOnRampApi.ts

## Question
poll checks a caller-supplied AbortSignal; can an attacker abort CoinbaseOnRampApi.initOnRampSession at a chosen moment so the app treats a completed deposit as aborted and issues a duplicate?

## Target
- File/function: [src/client/funding/CoinbaseOnRampApi.ts](src/client/funding/CoinbaseOnRampApi.ts) - CoinbaseOnRampApi.initOnRampSession, getStatus(partnerUserId)
- Entrypoint: privy.funding.coinbase.initOnRampSession(input)
- Attacker controls: the init body (addresses, assets, amount) and partnerUserId query value
- Exploit idea: Abort right after the funds land.
- Invariant to test: Abort must not change the recorded outcome of a settled deposit.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Integration test: abort CoinbaseOnRampApi.initOnRampSession after settlement and assert the state reflects settlement.
