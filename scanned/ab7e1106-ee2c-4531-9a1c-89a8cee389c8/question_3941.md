# Q3941: wallet creation failure hidden in coinbase.ts

## Question
The refund path returns REFUND_WALLET_CREATION_FAILED from a bare catch; can an attacker force that failure in getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId and have the deposit created with a missing or stale refund address?

## Target
- File/function: [src/funding/coinbase.ts](src/funding/coinbase.ts) - getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId, sessionToken, presetCryptoAmount, defaultNetwork, defaultAsset, partnerUserId), toCoinbaseBlockchainFromChainId, toCoinbaseAssetId, ASSET_ID_MAP, formatAmount('.'-prefix fix)
- Entrypoint: the on-ramp URL handed to the user's browser
- Attacker controls: amount string, chainId, asset, experience, and the session token/partner id echoed into the URL
- Exploit idea: Fail the create route and inspect the resulting quote body.
- Invariant to test: A deposit must not be created without a valid refund address.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: fail the create route and assert getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId aborts the quote.
