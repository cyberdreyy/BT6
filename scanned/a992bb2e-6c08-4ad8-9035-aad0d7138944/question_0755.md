# Q0755: polling accepts any order for the address in getSolanaUsdcMintAddressForCluster.ts

## Question
waitForDeposit polls GetNextDepositAddressOrder with a deposit address id and an `after` timestamp, then fetches whatever order id comes back; can an attacker cause getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster to bind to an order that is not the user's deposit?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Return a next-order response naming a foreign order id.
- Invariant to test: Polled orders must be verified to belong to the requesting deposit and user.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return a foreign order id in getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster's stub and assert it is rejected.
