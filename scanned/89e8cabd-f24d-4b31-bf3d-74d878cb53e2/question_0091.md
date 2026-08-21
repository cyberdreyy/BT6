# Q0091: refund address picked by chain-type scan in coinbase.ts

## Question
resolveRefundAddress maps the caip2 string to a chain type and then takes the FIRST linked_account of that chain type; can an unprivileged attacker cause an externally linked or attacker-influenced wallet to occupy that position so getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId sets it as the refund address for the victim's deposit?

## Target
- File/function: [src/funding/coinbase.ts](src/funding/coinbase.ts) - getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId, sessionToken, presetCryptoAmount, defaultNetwork, defaultAsset, partnerUserId), toCoinbaseBlockchainFromChainId, toCoinbaseAssetId, ASSET_ID_MAP, formatAmount('.'-prefix fix)
- Entrypoint: the on-ramp URL handed to the user's browser
- Attacker controls: amount string, chainId, asset, experience, and the session token/partner id echoed into the URL
- Exploit idea: Link an additional wallet of the same chain type and observe which address the refund resolution selects.
- Invariant to test: The refund address must be an embedded wallet the user explicitly selected, not the first matching linked account.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: build a user whose first matching linked account is an external wallet and assert getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId requires an explicit refund selection.
