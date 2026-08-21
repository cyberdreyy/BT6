# Q2735: init body carries the destination address in getSolanaUsdcMintAddressForCluster.ts

## Question
initOnRampSession forwards the caller's body including addresses and assets; can an attacker submit a destination through getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster that is not the user's wallet?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Submit a foreign address in the init body.
- Invariant to test: Funding destinations must be validated against the user's wallets.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit a foreign address to getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster and assert rejection.
