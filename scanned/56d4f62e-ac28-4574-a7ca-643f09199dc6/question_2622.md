# Q2622: coinbase status by partner user id in moonpay.ts

## Question
CoinbaseOnRampApi.getStatus takes a partnerUserId query value from the caller; can an attacker pass another user's partner id through isSupportedChainIdForMoonpay and read their funding status?

## Target
- File/function: [src/funding/moonpay.ts](src/funding/moonpay.ts) - isSupportedChainIdForMoonpay, chainToMoonpayCurrency (defaults to ETH_ETHEREUM on unknown chain), fundingMethodToMoonpayPaymentMethod
- Entrypoint: MoonPay funding parameter construction
- Attacker controls: chainId and asset arguments
- Exploit idea: Call getStatus with a foreign partner id.
- Invariant to test: Status lookups must be scoped to the authenticated user.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Integration test: call isSupportedChainIdForMoonpay with a foreign id and assert refusal.
