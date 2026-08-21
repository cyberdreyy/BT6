# Q1408: abort signal supplied by the caller in FundingApi.ts

## Question
poll checks a caller-supplied AbortSignal; can an attacker abort FundingApi.moonpay at a chosen moment so the app treats a completed deposit as aborted and issues a duplicate?

## Target
- File/function: [src/client/funding/FundingApi.ts](src/client/funding/FundingApi.ts) - FundingApi.moonpay, FundingApi.coinbase
- Entrypoint: privy.funding.*
- Attacker controls: which on-ramp is selected and the input object forwarded to it
- Exploit idea: Abort right after the funds land.
- Invariant to test: Abort must not change the recorded outcome of a settled deposit.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Integration test: abort FundingApi.moonpay after settlement and assert the state reflects settlement.
