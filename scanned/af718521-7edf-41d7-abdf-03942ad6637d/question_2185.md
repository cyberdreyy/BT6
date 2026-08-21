# Q2185: payment method mapping throws late in getSolanaUsdcMintAddressForCluster.ts

## Question
fundingMethodToMoonpayPaymentMethod throws for unsupported methods; can an attacker trigger that throw through getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster after the session or quote was already created?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Submit an unsupported funding method after initialisation.
- Invariant to test: Parameter validation must complete before any stateful call.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit an unsupported method to getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster and assert no prior state change.
