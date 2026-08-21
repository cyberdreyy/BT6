# Q0638: source and destination currency unchecked in FundingApi.ts

## Question
The quote body accepts source_currency and destination_currency as opaque strings; can an attacker submit a pair through FundingApi.moonpay that the client never validates against getConfig, so the user approves a route they did not intend?

## Target
- File/function: [src/client/funding/FundingApi.ts](src/client/funding/FundingApi.ts) - FundingApi.moonpay, FundingApi.coinbase
- Entrypoint: privy.funding.*
- Attacker controls: which on-ramp is selected and the input object forwarded to it
- Exploit idea: Submit currencies absent from the deposit config.
- Invariant to test: Quote parameters must be validated against the fetched deposit configuration.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit unsupported currencies to FundingApi.moonpay and assert client-side validation.
