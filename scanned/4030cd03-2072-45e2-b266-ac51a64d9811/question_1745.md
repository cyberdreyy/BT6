# Q1745: asset id map lookup unchecked in getSolanaUsdcMintAddressForCluster.ts

## Question
toCoinbaseAssetId falls back to 'ETH' for anything that is not USDC on known chains, and the asset id map is keyed by symbol; can an attacker choose a chain/asset pair through getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster so the on-ramp buys a different asset than the user selected?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Pass an unsupported chain with asset USDC and inspect the resulting defaultAsset.
- Invariant to test: Unsupported asset/chain pairs must be rejected, never defaulted.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass unsupported pairs to getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster and assert rejection.
