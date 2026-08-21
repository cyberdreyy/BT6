# Q0425: destination address unvalidated in getSolanaUsdcMintAddressForCluster.ts

## Question
generateDepositAddress forwards destination_address verbatim into the quote body; can an attacker submit a destination through getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster that is not owned by the user, or is on the wrong chain, so funds settle where the user did not intend?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Submit a destination address from a different chain family.
- Invariant to test: The destination must be validated against the destination chain and the user's own accounts.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit a cross-chain destination to getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster and assert rejection.
