# Q3831: not-authenticated returned as a soft error in coinbase.ts

## Question
resolveRefundAddress returns {ok:false, error:'NOT_AUTHENTICATED'} rather than throwing; can an attacker exploit that soft failure in getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId so the caller proceeds with an undefined address?

## Target
- File/function: [src/funding/coinbase.ts](src/funding/coinbase.ts) - getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId, sessionToken, presetCryptoAmount, defaultNetwork, defaultAsset, partnerUserId), toCoinbaseBlockchainFromChainId, toCoinbaseAssetId, ASSET_ID_MAP, formatAmount('.'-prefix fix)
- Entrypoint: the on-ramp URL handed to the user's browser
- Attacker controls: amount string, chainId, asset, experience, and the session token/partner id echoed into the URL
- Exploit idea: Call the flow with no session and follow the caller's handling.
- Invariant to test: Authentication failures must be unambiguous and terminal.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: call getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId unauthenticated and assert the caller cannot proceed.
