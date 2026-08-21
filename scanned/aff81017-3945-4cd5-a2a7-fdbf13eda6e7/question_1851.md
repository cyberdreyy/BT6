# Q1851: network defaulted on unknown chain in coinbase.ts

## Question
toCoinbaseBlockchainFromChainId returns undefined for unknown chains while the URL builder still sets defaultNetwork; can an attacker use getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId so the on-ramp delivers funds on an unintended network?

## Target
- File/function: [src/funding/coinbase.ts](src/funding/coinbase.ts) - getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId, sessionToken, presetCryptoAmount, defaultNetwork, defaultAsset, partnerUserId), toCoinbaseBlockchainFromChainId, toCoinbaseAssetId, ASSET_ID_MAP, formatAmount('.'-prefix fix)
- Entrypoint: the on-ramp URL handed to the user's browser
- Attacker controls: amount string, chainId, asset, experience, and the session token/partner id echoed into the URL
- Exploit idea: Pass an unsupported chainId through the funding path.
- Invariant to test: An unknown chain must abort the funding flow.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass an unsupported chainId to getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId and assert abort.
