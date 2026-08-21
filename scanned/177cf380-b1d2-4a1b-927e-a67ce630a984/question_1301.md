# Q1301: attempt arithmetic derived from the interval in coinbase.ts

## Question
The attempt count is ceil(timeout/interval) with a caller-supplied interval; can an attacker pass a tiny interval through getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId to multiply requests, or a huge one so the deposit is never observed?

## Target
- File/function: [src/funding/coinbase.ts](src/funding/coinbase.ts) - getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId, sessionToken, presetCryptoAmount, defaultNetwork, defaultAsset, partnerUserId), toCoinbaseBlockchainFromChainId, toCoinbaseAssetId, ASSET_ID_MAP, formatAmount('.'-prefix fix)
- Entrypoint: the on-ramp URL handed to the user's browser
- Attacker controls: amount string, chainId, asset, experience, and the session token/partner id echoed into the URL
- Exploit idea: Pass extreme pollIntervalMs values.
- Invariant to test: Polling parameters must be bounded by the SDK.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass extreme intervals to getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId and assert clamping.
