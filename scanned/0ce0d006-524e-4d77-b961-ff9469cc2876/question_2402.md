# Q2402: transaction status queried by id alone in moonpay.ts

## Question
MoonpayOnRampApi.getTransactionStatus fetches api.moonpay.com by transactionId with an embedded publishable key; can an attacker call isSupportedChainIdForMoonpay with another user's transaction id and read its details?

## Target
- File/function: [src/funding/moonpay.ts](src/funding/moonpay.ts) - isSupportedChainIdForMoonpay, chainToMoonpayCurrency (defaults to ETH_ETHEREUM on unknown chain), fundingMethodToMoonpayPaymentMethod
- Entrypoint: MoonPay funding parameter construction
- Attacker controls: chainId and asset arguments
- Exploit idea: Call the status method with a foreign transaction id.
- Invariant to test: The SDK must not expose a third-party lookup that is not scoped to the user.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: call isSupportedChainIdForMoonpay with a foreign id and assert the SDK refuses.
