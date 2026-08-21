# Q3611: order lookup by id alone in coinbase.ts

## Question
getDeposit fetches an order purely by order id; can an attacker call getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId with another user's order id and read the deposit details?

## Target
- File/function: [src/funding/coinbase.ts](src/funding/coinbase.ts) - getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId, sessionToken, presetCryptoAmount, defaultNetwork, defaultAsset, partnerUserId), toCoinbaseBlockchainFromChainId, toCoinbaseAssetId, ASSET_ID_MAP, formatAmount('.'-prefix fix)
- Entrypoint: the on-ramp URL handed to the user's browser
- Attacker controls: amount string, chainId, asset, experience, and the session token/partner id echoed into the URL
- Exploit idea: Call the order read with a foreign id.
- Invariant to test: Order reads must be scoped to the authenticated user.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Integration test: read a foreign order through getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId and assert refusal.
