# Q0861: quoteCreatedAt is a client cursor in coinbase.ts

## Question
The `after` query is the caller's quoteCreatedAt; can an attacker pass a cursor through getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId that surfaces an older or unrelated order as the user's deposit?

## Target
- File/function: [src/funding/coinbase.ts](src/funding/coinbase.ts) - getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId, sessionToken, presetCryptoAmount, defaultNetwork, defaultAsset, partnerUserId), toCoinbaseBlockchainFromChainId, toCoinbaseAssetId, ASSET_ID_MAP, formatAmount('.'-prefix fix)
- Entrypoint: the on-ramp URL handed to the user's browser
- Attacker controls: amount string, chainId, asset, experience, and the session token/partner id echoed into the URL
- Exploit idea: Pass a much earlier cursor and observe the order returned.
- Invariant to test: The polling cursor must be server-issued and bound to the quote.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass a stale cursor to getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId and assert it is refused.
