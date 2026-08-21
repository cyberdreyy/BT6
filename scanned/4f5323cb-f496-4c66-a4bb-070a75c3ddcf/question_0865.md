# Q0865: quoteCreatedAt is a client cursor in getSolanaUsdcMintAddressForCluster.ts

## Question
The `after` query is the caller's quoteCreatedAt; can an attacker pass a cursor through getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster that surfaces an older or unrelated order as the user's deposit?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Pass a much earlier cursor and observe the order returned.
- Invariant to test: The polling cursor must be server-issued and bound to the quote.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass a stale cursor to getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster and assert it is refused.
