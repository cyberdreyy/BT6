# Q3721: onWalletCreated callback fires before confirmation in coinbase.ts

## Question
resolveRefundAddress invokes onWalletCreated after the create call returns; can an attacker use getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId so the app treats an unconfirmed wallet as ready and routes funds to it?

## Target
- File/function: [src/funding/coinbase.ts](src/funding/coinbase.ts) - getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId, sessionToken, presetCryptoAmount, defaultNetwork, defaultAsset, partnerUserId), toCoinbaseBlockchainFromChainId, toCoinbaseAssetId, ASSET_ID_MAP, formatAmount('.'-prefix fix)
- Entrypoint: the on-ramp URL handed to the user's browser
- Attacker controls: amount string, chainId, asset, experience, and the session token/partner id echoed into the URL
- Exploit idea: Return a create response and inspect the callback timing versus session refresh.
- Invariant to test: Callbacks signalling readiness must follow a confirmed session refresh.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId refreshes the user before invoking the callback.
