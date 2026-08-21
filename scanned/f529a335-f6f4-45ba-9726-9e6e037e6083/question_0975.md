# Q0975: completion decided by a status string in getSolanaUsdcMintAddressForCluster.ts

## Question
waitForCompletion polls until status !== 'executing' and reports success for any other value; can an attacker cause getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster to report success for a failed, refunded or cancelled order?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Return a terminal status other than success and inspect the mapped result.
- Invariant to test: Only an explicit success status may be reported as success.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: enumerate terminal statuses through getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster and assert only success maps to success.
