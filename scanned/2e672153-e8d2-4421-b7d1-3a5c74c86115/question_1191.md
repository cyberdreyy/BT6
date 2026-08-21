# Q1191: poll swallows every operation error in coinbase.ts

## Question
poll catches all errors, records the last one and keeps iterating; can an attacker cause repeated authorization failures inside getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId to be hidden until max_attempts, so the app keeps polling with a stale session?

## Target
- File/function: [src/funding/coinbase.ts](src/funding/coinbase.ts) - getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId, sessionToken, presetCryptoAmount, defaultNetwork, defaultAsset, partnerUserId), toCoinbaseBlockchainFromChainId, toCoinbaseAssetId, ASSET_ID_MAP, formatAmount('.'-prefix fix)
- Entrypoint: the on-ramp URL handed to the user's browser
- Attacker controls: amount string, chainId, asset, experience, and the session token/partner id echoed into the URL
- Exploit idea: Return 401s from the polled route and observe the loop behaviour.
- Invariant to test: Authorization failures must terminate polling immediately.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return 401 from getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId's operation and assert immediate termination.
