# Q0858: quoteCreatedAt is a client cursor in FundingApi.ts

## Question
The `after` query is the caller's quoteCreatedAt; can an attacker pass a cursor through FundingApi.moonpay that surfaces an older or unrelated order as the user's deposit?

## Target
- File/function: [src/client/funding/FundingApi.ts](src/client/funding/FundingApi.ts) - FundingApi.moonpay, FundingApi.coinbase
- Entrypoint: privy.funding.*
- Attacker controls: which on-ramp is selected and the input object forwarded to it
- Exploit idea: Pass a much earlier cursor and observe the order returned.
- Invariant to test: The polling cursor must be server-issued and bound to the quote.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass a stale cursor to FundingApi.moonpay and assert it is refused.
