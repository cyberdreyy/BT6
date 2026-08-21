# Q2728: init body carries the destination address in FundingApi.ts

## Question
initOnRampSession forwards the caller's body including addresses and assets; can an attacker submit a destination through FundingApi.moonpay that is not the user's wallet?

## Target
- File/function: [src/client/funding/FundingApi.ts](src/client/funding/FundingApi.ts) - FundingApi.moonpay, FundingApi.coinbase
- Entrypoint: privy.funding.*
- Attacker controls: which on-ramp is selected and the input object forwarded to it
- Exploit idea: Submit a foreign address in the init body.
- Invariant to test: Funding destinations must be validated against the user's wallets.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit a foreign address to FundingApi.moonpay and assert rejection.
