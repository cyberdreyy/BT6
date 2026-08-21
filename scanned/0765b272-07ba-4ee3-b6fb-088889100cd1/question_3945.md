# Q3945: wallet creation failure hidden in getSolanaUsdcMintAddressForCluster.ts

## Question
The refund path returns REFUND_WALLET_CREATION_FAILED from a bare catch; can an attacker force that failure in getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster and have the deposit created with a missing or stale refund address?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Fail the create route and inspect the resulting quote body.
- Invariant to test: A deposit must not be created without a valid refund address.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: fail the create route and assert getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster aborts the quote.
