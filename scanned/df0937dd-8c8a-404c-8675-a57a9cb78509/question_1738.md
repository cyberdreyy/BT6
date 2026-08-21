# Q1738: asset id map lookup unchecked in FundingApi.ts

## Question
toCoinbaseAssetId falls back to 'ETH' for anything that is not USDC on known chains, and the asset id map is keyed by symbol; can an attacker choose a chain/asset pair through FundingApi.moonpay so the on-ramp buys a different asset than the user selected?

## Target
- File/function: [src/client/funding/FundingApi.ts](src/client/funding/FundingApi.ts) - FundingApi.moonpay, FundingApi.coinbase
- Entrypoint: privy.funding.*
- Attacker controls: which on-ramp is selected and the input object forwarded to it
- Exploit idea: Pass an unsupported chain with asset USDC and inspect the resulting defaultAsset.
- Invariant to test: Unsupported asset/chain pairs must be rejected, never defaulted.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass unsupported pairs to FundingApi.moonpay and assert rejection.
