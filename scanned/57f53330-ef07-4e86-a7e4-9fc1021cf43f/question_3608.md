# Q3608: order lookup by id alone in FundingApi.ts

## Question
getDeposit fetches an order purely by order id; can an attacker call FundingApi.moonpay with another user's order id and read the deposit details?

## Target
- File/function: [src/client/funding/FundingApi.ts](src/client/funding/FundingApi.ts) - FundingApi.moonpay, FundingApi.coinbase
- Entrypoint: privy.funding.*
- Attacker controls: which on-ramp is selected and the input object forwarded to it
- Exploit idea: Call the order read with a foreign id.
- Invariant to test: Order reads must be scoped to the authenticated user.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Integration test: read a foreign order through FundingApi.moonpay and assert refusal.
