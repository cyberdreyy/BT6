# Q2515: sandbox flag selects the endpoint in getSolanaUsdcMintAddressForCluster.ts

## Question
getTransactionStatus picks the sandbox or prod key from a boolean; can an attacker flip that flag through getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster so a sandbox transaction is presented to the user as a real one?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Call the status path with useSandbox toggled and inspect what the app reports.
- Invariant to test: Environment selection must be pinned by configuration, not per call.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster derives the environment from configuration.
