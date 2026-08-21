# Q0971: completion decided by a status string in coinbase.ts

## Question
waitForCompletion polls until status !== 'executing' and reports success for any other value; can an attacker cause getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId to report success for a failed, refunded or cancelled order?

## Target
- File/function: [src/funding/coinbase.ts](src/funding/coinbase.ts) - getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId, sessionToken, presetCryptoAmount, defaultNetwork, defaultAsset, partnerUserId), toCoinbaseBlockchainFromChainId, toCoinbaseAssetId, ASSET_ID_MAP, formatAmount('.'-prefix fix)
- Entrypoint: the on-ramp URL handed to the user's browser
- Attacker controls: amount string, chainId, asset, experience, and the session token/partner id echoed into the URL
- Exploit idea: Return a terminal status other than success and inspect the mapped result.
- Invariant to test: Only an explicit success status may be reported as success.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: enumerate terminal statuses through getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId and assert only success maps to success.
