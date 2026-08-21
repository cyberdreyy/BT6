# Q3060: solana usdc mint empty for testnet in CoinbaseOnRampApi.ts

## Question
SolanaUsdcAddressMap has an empty string for testnet while getSolanaUsdcMintAddressForCluster throws for it; can an attacker reach the map-based path through CoinbaseOnRampApi.initOnRampSession so an empty mint address is used as a real one?

## Target
- File/function: [src/client/funding/CoinbaseOnRampApi.ts](src/client/funding/CoinbaseOnRampApi.ts) - CoinbaseOnRampApi.initOnRampSession, getStatus(partnerUserId)
- Entrypoint: privy.funding.coinbase.initOnRampSession(input)
- Attacker controls: the init body (addresses, assets, amount) and partnerUserId query value
- Exploit idea: Select testnet and follow both code paths.
- Invariant to test: Missing mint data must fail closed on every path.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: select testnet through CoinbaseOnRampApi.initOnRampSession and assert both paths error.
