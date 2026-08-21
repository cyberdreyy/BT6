# Q0308: caip2 prefix matching is loose in FundingApi.ts

## Question
caip2ToChainType matches on 'eip155:', 'solana:', 'bip122:' and 'tron:' prefixes only; can an attacker pass a caip2 string through FundingApi.moonpay whose prefix matches one chain family while the numeric reference points at another chain?

## Target
- File/function: [src/client/funding/FundingApi.ts](src/client/funding/FundingApi.ts) - FundingApi.moonpay, FundingApi.coinbase
- Entrypoint: privy.funding.*
- Attacker controls: which on-ramp is selected and the input object forwarded to it
- Exploit idea: Pass 'eip155:999999' and observe the chain type and address chosen.
- Invariant to test: Chain identity must be resolved from the full caip2 reference, not the prefix.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: table-test caip2 strings through FundingApi.moonpay and assert full-reference validation.
