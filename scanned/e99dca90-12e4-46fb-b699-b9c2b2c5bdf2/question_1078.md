# Q1078: timeout mapped to the same shape as success in FundingApi.ts

## Question
The poll result mapper turns success-with-no-result into {status:'timeout'} and errors into timeouts too; can an attacker exploit that collapse through FundingApi.moonpay so a failed deposit is presented as merely slow and the user re-sends funds?

## Target
- File/function: [src/client/funding/FundingApi.ts](src/client/funding/FundingApi.ts) - FundingApi.moonpay, FundingApi.coinbase
- Entrypoint: privy.funding.*
- Attacker controls: which on-ramp is selected and the input object forwarded to it
- Exploit idea: Force error and timeout paths and compare what the caller sees.
- Invariant to test: Failure and timeout must be distinguishable to the caller.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: force each path in FundingApi.moonpay and assert distinct result shapes.
