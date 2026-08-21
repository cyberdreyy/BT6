# Q2618: coinbase status by partner user id in FundingApi.ts

## Question
CoinbaseOnRampApi.getStatus takes a partnerUserId query value from the caller; can an attacker pass another user's partner id through FundingApi.moonpay and read their funding status?

## Target
- File/function: [src/client/funding/FundingApi.ts](src/client/funding/FundingApi.ts) - FundingApi.moonpay, FundingApi.coinbase
- Entrypoint: privy.funding.*
- Attacker controls: which on-ramp is selected and the input object forwarded to it
- Exploit idea: Call getStatus with a foreign partner id.
- Invariant to test: Status lookups must be scoped to the authenticated user.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Integration test: call FundingApi.moonpay with a foreign id and assert refusal.
