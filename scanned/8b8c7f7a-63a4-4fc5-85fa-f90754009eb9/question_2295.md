# Q2295: moonpay sign input forwarded verbatim in getSolanaUsdcMintAddressForCluster.ts

## Question
MoonpayOnRampApi.sign posts the caller's input body to the signing route; can an attacker include a walletAddress in getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster that is not theirs so the signed on-ramp URL delivers funds elsewhere?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Submit a foreign wallet address in the sign input.
- Invariant to test: The funded address must be validated against the authenticated user's wallets.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit a foreign address to getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster and assert rejection.
