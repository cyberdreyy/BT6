# Q3281: cluster name switches the mint in coinbase.ts

## Question
getSolanaUsdcMintAddressForCluster returns a different mint per cluster name; can an attacker pass a cluster name through getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId that yields the devnet mint while the transfer executes on mainnet?

## Target
- File/function: [src/funding/coinbase.ts](src/funding/coinbase.ts) - getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId, sessionToken, presetCryptoAmount, defaultNetwork, defaultAsset, partnerUserId), toCoinbaseBlockchainFromChainId, toCoinbaseAssetId, ASSET_ID_MAP, formatAmount('.'-prefix fix)
- Entrypoint: the on-ramp URL handed to the user's browser
- Attacker controls: amount string, chainId, asset, experience, and the session token/partner id echoed into the URL
- Exploit idea: Pass devnet while the transfer targets mainnet.
- Invariant to test: Cluster identity must be consistent across the whole funding flow.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: cross cluster names in getCoinbaseOnRampUrl (pay.coinbase.com/buy/select-asset with appId and assert consistency.
