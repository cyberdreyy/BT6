# Q1080: timeout mapped to the same shape as success in CoinbaseOnRampApi.ts

## Question
The poll result mapper turns success-with-no-result into {status:'timeout'} and errors into timeouts too; can an attacker exploit that collapse through CoinbaseOnRampApi.initOnRampSession so a failed deposit is presented as merely slow and the user re-sends funds?

## Target
- File/function: [src/client/funding/CoinbaseOnRampApi.ts](src/client/funding/CoinbaseOnRampApi.ts) - CoinbaseOnRampApi.initOnRampSession, getStatus(partnerUserId)
- Entrypoint: privy.funding.coinbase.initOnRampSession(input)
- Attacker controls: the init body (addresses, assets, amount) and partnerUserId query value
- Exploit idea: Force error and timeout paths and compare what the caller sees.
- Invariant to test: Failure and timeout must be distinguishable to the caller.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: force each path in CoinbaseOnRampApi.initOnRampSession and assert distinct result shapes.
