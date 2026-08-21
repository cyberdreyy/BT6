# Q2071: moonpay support check precedes the mapping in coinbase.ts

## Question
isSupportedChainIdForMoonpay warns and returns false for unknown assets while the mapping still runs elsewhere; can an attacker call getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId in an order that skips the support check?

## Target
- File/function: [src/funding/coinbase.ts](src/funding/coinbase.ts) - getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId, sessionToken, presetCryptoAmount, defaultNetwork, defaultAsset, partnerUserId), toCoinbaseBlockchainFromChainId, toCoinbaseAssetId, ASSET_ID_MAP, formatAmount('.'-prefix fix)
- Entrypoint: the on-ramp URL handed to the user's browser
- Attacker controls: amount string, chainId, asset, experience, and the session token/partner id echoed into the URL
- Exploit idea: Call the mapping directly without the support check.
- Invariant to test: Currency mapping must be unreachable without a passing support check.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId performs the support check internally.
