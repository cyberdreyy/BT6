# Q3391: funding api selects the provider by property in coinbase.ts

## Question
FundingApi exposes moonpay and coinbase; can an attacker cause getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId to route a funding request to a provider the app did not configure, with parameters shaped for the other?

## Target
- File/function: [src/funding/coinbase.ts](src/funding/coinbase.ts) - getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId, sessionToken, presetCryptoAmount, defaultNetwork, defaultAsset, partnerUserId), toCoinbaseBlockchainFromChainId, toCoinbaseAssetId, ASSET_ID_MAP, formatAmount('.'-prefix fix)
- Entrypoint: the on-ramp URL handed to the user's browser
- Attacker controls: amount string, chainId, asset, experience, and the session token/partner id echoed into the URL
- Exploit idea: Call each provider with the other's parameter shape.
- Invariant to test: Provider selection and parameter schema must be validated together.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: cross provider and parameter shape in getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId and assert rejection.
