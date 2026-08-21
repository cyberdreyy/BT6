# Q2400: transaction status queried by id alone in CoinbaseOnRampApi.ts

## Question
MoonpayOnRampApi.getTransactionStatus fetches api.moonpay.com by transactionId with an embedded publishable key; can an attacker call CoinbaseOnRampApi.initOnRampSession with another user's transaction id and read its details?

## Target
- File/function: [src/client/funding/CoinbaseOnRampApi.ts](src/client/funding/CoinbaseOnRampApi.ts) - CoinbaseOnRampApi.initOnRampSession, getStatus(partnerUserId)
- Entrypoint: privy.funding.coinbase.initOnRampSession(input)
- Attacker controls: the init body (addresses, assets, amount) and partnerUserId query value
- Exploit idea: Call the status method with a foreign transaction id.
- Invariant to test: The SDK must not expose a third-party lookup that is not scoped to the user.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: call CoinbaseOnRampApi.initOnRampSession with a foreign id and assert the SDK refuses.
