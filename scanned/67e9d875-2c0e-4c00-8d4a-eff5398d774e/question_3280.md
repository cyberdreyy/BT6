# Q3280: cluster name switches the mint in CoinbaseOnRampApi.ts

## Question
getSolanaUsdcMintAddressForCluster returns a different mint per cluster name; can an attacker pass a cluster name through CoinbaseOnRampApi.initOnRampSession that yields the devnet mint while the transfer executes on mainnet?

## Target
- File/function: [src/client/funding/CoinbaseOnRampApi.ts](src/client/funding/CoinbaseOnRampApi.ts) - CoinbaseOnRampApi.initOnRampSession, getStatus(partnerUserId)
- Entrypoint: privy.funding.coinbase.initOnRampSession(input)
- Attacker controls: the init body (addresses, assets, amount) and partnerUserId query value
- Exploit idea: Pass devnet while the transfer targets mainnet.
- Invariant to test: Cluster identity must be consistent across the whole funding flow.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: cross cluster names in CoinbaseOnRampApi.initOnRampSession and assert consistency.
