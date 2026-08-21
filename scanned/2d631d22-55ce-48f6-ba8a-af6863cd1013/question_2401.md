# Q2401: transaction status queried by id alone in coinbase.ts

## Question
MoonpayOnRampApi.getTransactionStatus fetches api.moonpay.com by transactionId with an embedded publishable key; can an attacker call getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId with another user's transaction id and read its details?

## Target
- File/function: [src/funding/coinbase.ts](src/funding/coinbase.ts) - getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId, sessionToken, presetCryptoAmount, defaultNetwork, defaultAsset, partnerUserId), toCoinbaseBlockchainFromChainId, toCoinbaseAssetId, ASSET_ID_MAP, formatAmount('.'-prefix fix)
- Entrypoint: the on-ramp URL handed to the user's browser
- Attacker controls: amount string, chainId, asset, experience, and the session token/partner id echoed into the URL
- Exploit idea: Call the status method with a foreign transaction id.
- Invariant to test: The SDK must not expose a third-party lookup that is not scoped to the user.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: call getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId with a foreign id and assert the SDK refuses.
