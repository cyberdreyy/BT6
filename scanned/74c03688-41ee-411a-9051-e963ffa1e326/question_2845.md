# Q2845: usdc detection by exact address equality in getSolanaUsdcMintAddressForCluster.ts

## Question
getIsTokenUsdc compares the supplied address to UsdcAddressMap[chain.id] with ===; can an attacker pass a checksummed or padded variant through getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster so a genuine USDC transfer is classified as an unknown token (or a lookalike is treated as USDC)?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Pass mixed-case and zero-padded variants of the USDC address.
- Invariant to test: Token identity comparison must be canonical.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: table-test address forms through getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster.
