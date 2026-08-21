# Q2508: sandbox flag selects the endpoint in FundingApi.ts

## Question
getTransactionStatus picks the sandbox or prod key from a boolean; can an attacker flip that flag through FundingApi.moonpay so a sandbox transaction is presented to the user as a real one?

## Target
- File/function: [src/client/funding/FundingApi.ts](src/client/funding/FundingApi.ts) - FundingApi.moonpay, FundingApi.coinbase
- Entrypoint: privy.funding.*
- Attacker controls: which on-ramp is selected and the input object forwarded to it
- Exploit idea: Call the status path with useSandbox toggled and inspect what the app reports.
- Invariant to test: Environment selection must be pinned by configuration, not per call.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert FundingApi.moonpay derives the environment from configuration.
