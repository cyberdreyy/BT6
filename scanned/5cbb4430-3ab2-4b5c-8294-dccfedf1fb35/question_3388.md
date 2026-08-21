# Q3388: funding api selects the provider by property in FundingApi.ts

## Question
FundingApi exposes moonpay and coinbase; can an attacker cause FundingApi.moonpay to route a funding request to a provider the app did not configure, with parameters shaped for the other?

## Target
- File/function: [src/client/funding/FundingApi.ts](src/client/funding/FundingApi.ts) - FundingApi.moonpay, FundingApi.coinbase
- Entrypoint: privy.funding.*
- Attacker controls: which on-ramp is selected and the input object forwarded to it
- Exploit idea: Call each provider with the other's parameter shape.
- Invariant to test: Provider selection and parameter schema must be validated together.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: cross provider and parameter shape in FundingApi.moonpay and assert rejection.
