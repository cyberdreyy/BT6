# Q3389: funding api selects the provider by property in MoonpayOnRampApi.ts

## Question
FundingApi exposes moonpay and coinbase; can an attacker cause MoonpayOnRampApi.sign (MoonpayOnRampSign) to route a funding request to a provider the app did not configure, with parameters shaped for the other?

## Target
- File/function: [src/client/funding/MoonpayOnRampApi.ts](src/client/funding/MoonpayOnRampApi.ts) - MoonpayOnRampApi.sign (MoonpayOnRampSign), getTransactionStatus (direct api.moonpay.com fetch with embedded pk_live key)
- Entrypoint: privy.funding.moonpay.sign(input) / getTransactionStatus({transactionId, useSandbox})
- Attacker controls: the sign input body (walletAddress, currency, amount) and transactionId
- Exploit idea: Call each provider with the other's parameter shape.
- Invariant to test: Provider selection and parameter schema must be validated together.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: cross provider and parameter shape in MoonpayOnRampApi.sign (MoonpayOnRampSign) and assert rejection.
