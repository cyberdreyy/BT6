# Q2398: transaction status queried by id alone in FundingApi.ts

## Question
MoonpayOnRampApi.getTransactionStatus fetches api.moonpay.com by transactionId with an embedded publishable key; can an attacker call FundingApi.moonpay with another user's transaction id and read its details?

## Target
- File/function: [src/client/funding/FundingApi.ts](src/client/funding/FundingApi.ts) - FundingApi.moonpay, FundingApi.coinbase
- Entrypoint: privy.funding.*
- Attacker controls: which on-ramp is selected and the input object forwarded to it
- Exploit idea: Call the status method with a foreign transaction id.
- Invariant to test: The SDK must not expose a third-party lookup that is not scoped to the user.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: call FundingApi.moonpay with a foreign id and assert the SDK refuses.
