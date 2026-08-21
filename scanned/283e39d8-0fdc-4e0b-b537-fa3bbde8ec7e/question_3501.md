# Q3501: deposit config fetched but not enforced in coinbase.ts

## Question
getConfig returns currencies and chains but the generate path does not consult it; can an attacker submit a quote through getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId for a pair the config excludes?

## Target
- File/function: [src/funding/coinbase.ts](src/funding/coinbase.ts) - getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId, sessionToken, presetCryptoAmount, defaultNetwork, defaultAsset, partnerUserId), toCoinbaseBlockchainFromChainId, toCoinbaseAssetId, ASSET_ID_MAP, formatAmount('.'-prefix fix)
- Entrypoint: the on-ramp URL handed to the user's browser
- Attacker controls: amount string, chainId, asset, experience, and the session token/partner id echoed into the URL
- Exploit idea: Submit an excluded pair after fetching the config.
- Invariant to test: The client must enforce the fetched configuration before creating a quote.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit an excluded pair to getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId and assert refusal.
