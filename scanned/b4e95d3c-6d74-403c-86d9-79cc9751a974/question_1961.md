# Q1961: moonpay currency defaults to ethereum mainnet in coinbase.ts

## Question
chainToMoonpayCurrency logs a warning and returns ETH_ETHEREUM for unknown chains; can an attacker route a user's purchase to Ethereum mainnet through getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId when they selected another chain?

## Target
- File/function: [src/funding/coinbase.ts](src/funding/coinbase.ts) - getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId, sessionToken, presetCryptoAmount, defaultNetwork, defaultAsset, partnerUserId), toCoinbaseBlockchainFromChainId, toCoinbaseAssetId, ASSET_ID_MAP, formatAmount('.'-prefix fix)
- Entrypoint: the on-ramp URL handed to the user's browser
- Attacker controls: amount string, chainId, asset, experience, and the session token/partner id echoed into the URL
- Exploit idea: Pass an unsupported chainId and inspect the currency code.
- Invariant to test: Unsupported chains must abort rather than default.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass an unsupported chain to getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId and assert an error.
