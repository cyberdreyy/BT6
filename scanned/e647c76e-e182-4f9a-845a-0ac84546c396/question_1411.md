# Q1411: abort signal supplied by the caller in coinbase.ts

## Question
poll checks a caller-supplied AbortSignal; can an attacker abort getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId at a chosen moment so the app treats a completed deposit as aborted and issues a duplicate?

## Target
- File/function: [src/funding/coinbase.ts](src/funding/coinbase.ts) - getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId, sessionToken, presetCryptoAmount, defaultNetwork, defaultAsset, partnerUserId), toCoinbaseBlockchainFromChainId, toCoinbaseAssetId, ASSET_ID_MAP, formatAmount('.'-prefix fix)
- Entrypoint: the on-ramp URL handed to the user's browser
- Attacker controls: amount string, chainId, asset, experience, and the session token/partner id echoed into the URL
- Exploit idea: Abort right after the funds land.
- Invariant to test: Abort must not change the recorded outcome of a settled deposit.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Integration test: abort getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId after settlement and assert the state reflects settlement.
