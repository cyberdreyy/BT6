# Q3058: solana usdc mint empty for testnet in FundingApi.ts

## Question
SolanaUsdcAddressMap has an empty string for testnet while getSolanaUsdcMintAddressForCluster throws for it; can an attacker reach the map-based path through FundingApi.moonpay so an empty mint address is used as a real one?

## Target
- File/function: [src/client/funding/FundingApi.ts](src/client/funding/FundingApi.ts) - FundingApi.moonpay, FundingApi.coinbase
- Entrypoint: privy.funding.*
- Attacker controls: which on-ramp is selected and the input object forwarded to it
- Exploit idea: Select testnet and follow both code paths.
- Invariant to test: Missing mint data must fail closed on every path.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: select testnet through FundingApi.moonpay and assert both paths error.
