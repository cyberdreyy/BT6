# Q2511: sandbox flag selects the endpoint in coinbase.ts

## Question
getTransactionStatus picks the sandbox or prod key from a boolean; can an attacker flip that flag through getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId so a sandbox transaction is presented to the user as a real one?

## Target
- File/function: [src/funding/coinbase.ts](src/funding/coinbase.ts) - getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId, sessionToken, presetCryptoAmount, defaultNetwork, defaultAsset, partnerUserId), toCoinbaseBlockchainFromChainId, toCoinbaseAssetId, ASSET_ID_MAP, formatAmount('.'-prefix fix)
- Entrypoint: the on-ramp URL handed to the user's browser
- Attacker controls: amount string, chainId, asset, experience, and the session token/partner id echoed into the URL
- Exploit idea: Call the status path with useSandbox toggled and inspect what the app reports.
- Invariant to test: Environment selection must be pinned by configuration, not per call.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId derives the environment from configuration.
