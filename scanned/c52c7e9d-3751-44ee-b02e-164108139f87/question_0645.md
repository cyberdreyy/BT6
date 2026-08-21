# Q0645: source and destination currency unchecked in getSolanaUsdcMintAddressForCluster.ts

## Question
The quote body accepts source_currency and destination_currency as opaque strings; can an attacker submit a pair through getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster that the client never validates against getConfig, so the user approves a route they did not intend?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Submit currencies absent from the deposit config.
- Invariant to test: Quote parameters must be validated against the fetched deposit configuration.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit unsupported currencies to getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster and assert client-side validation.
