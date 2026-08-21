# Q1855: network defaulted on unknown chain in getSolanaUsdcMintAddressForCluster.ts

## Question
toCoinbaseBlockchainFromChainId returns undefined for unknown chains while the URL builder still sets defaultNetwork; can an attacker use getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster so the on-ramp delivers funds on an unintended network?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Pass an unsupported chainId through the funding path.
- Invariant to test: An unknown chain must abort the funding flow.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass an unsupported chainId to getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster and assert abort.
