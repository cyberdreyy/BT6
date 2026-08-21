# Q1631: amount formatting patches leading dots in coinbase.ts

## Question
The amount helper rewrites a leading '.' to '0.' and otherwise passes the string through; can an attacker pass an amount through getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId (exponential, thousands separators, trailing characters) that the on-ramp parses differently than the app displayed?

## Target
- File/function: [src/funding/coinbase.ts](src/funding/coinbase.ts) - getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId, sessionToken, presetCryptoAmount, defaultNetwork, defaultAsset, partnerUserId), toCoinbaseBlockchainFromChainId, toCoinbaseAssetId, ASSET_ID_MAP, formatAmount('.'-prefix fix)
- Entrypoint: the on-ramp URL handed to the user's browser
- Attacker controls: amount string, chainId, asset, experience, and the session token/partner id echoed into the URL
- Exploit idea: Pass '1e3', '1,000' and '1.0abc' and inspect the URL value.
- Invariant to test: Amounts must be canonicalised and validated before they leave the SDK.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: table-test amount strings through getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId.
