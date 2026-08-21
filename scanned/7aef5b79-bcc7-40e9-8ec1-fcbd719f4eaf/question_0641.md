# Q0641: source and destination currency unchecked in coinbase.ts

## Question
The quote body accepts source_currency and destination_currency as opaque strings; can an attacker submit a pair through getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId that the client never validates against getConfig, so the user approves a route they did not intend?

## Target
- File/function: [src/funding/coinbase.ts](src/funding/coinbase.ts) - getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId, sessionToken, presetCryptoAmount, defaultNetwork, defaultAsset, partnerUserId), toCoinbaseBlockchainFromChainId, toCoinbaseAssetId, ASSET_ID_MAP, formatAmount('.'-prefix fix)
- Entrypoint: the on-ramp URL handed to the user's browser
- Attacker controls: amount string, chainId, asset, experience, and the session token/partner id echoed into the URL
- Exploit idea: Submit currencies absent from the deposit config.
- Invariant to test: Quote parameters must be validated against the fetched deposit configuration.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit unsupported currencies to getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId and assert client-side validation.
