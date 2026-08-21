# Q2844: usdc detection by exact address equality in getSolanaRpcEndpointForCluster.ts

## Question
getIsTokenUsdc compares the supplied address to UsdcAddressMap[chain.id] with ===; can an attacker pass a checksummed or padded variant through getSolanaRpcEndpointForCluster({name so a genuine USDC transfer is classified as an unknown token (or a lookalike is treated as USDC)?

## Target
- File/function: [src/solana/getSolanaRpcEndpointForCluster.ts](src/solana/getSolanaRpcEndpointForCluster.ts) - getSolanaRpcEndpointForCluster({name, rpcUrl}) - caller rpcUrl wins over the cluster default
- Entrypoint: SolanaClient construction for balance and mint reads
- Attacker controls: the cluster object passed in by the caller
- Exploit idea: Pass mixed-case and zero-padded variants of the USDC address.
- Invariant to test: Token identity comparison must be canonical.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: table-test address forms through getSolanaRpcEndpointForCluster({name.
