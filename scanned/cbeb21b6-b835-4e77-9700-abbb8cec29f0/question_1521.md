# Q1521: on-ramp url built from server values in coinbase.ts

## Question
getCoinbaseOnRampUrl embeds sessionToken, partnerUserId and appId from the init response into pay.coinbase.com query parameters; can an attacker influence the init response so getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId produces a URL that funds a different partner user?

## Target
- File/function: [src/funding/coinbase.ts](src/funding/coinbase.ts) - getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId, sessionToken, presetCryptoAmount, defaultNetwork, defaultAsset, partnerUserId), toCoinbaseBlockchainFromChainId, toCoinbaseAssetId, ASSET_ID_MAP, formatAmount('.'-prefix fix)
- Entrypoint: the on-ramp URL handed to the user's browser
- Attacker controls: amount string, chainId, asset, experience, and the session token/partner id echoed into the URL
- Exploit idea: Return an init response with a foreign partner_user_id and inspect the URL.
- Invariant to test: On-ramp URL parameters must be bound to the authenticated user's session.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return a foreign partner id and assert getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId refuses to build the URL.
