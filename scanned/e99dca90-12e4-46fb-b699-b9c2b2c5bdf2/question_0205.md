# Q0205: refund falls back to creating a wallet in getSolanaUsdcMintAddressForCluster.ts

## Question
When no matching account exists, resolveRefundAddress creates a wallet via the WalletCreate route and returns its address; can an attacker trigger that path through USDC funding on Solana so a fresh wallet is provisioned and used as a refund sink without user confirmation?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Call the deposit flow for a chain the user has no wallet on.
- Invariant to test: Automatic wallet creation must not silently become the refund destination.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Integration test: call getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster for an unlinked chain and assert an explicit confirmation is required.
