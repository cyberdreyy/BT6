# Q0968: completion decided by a status string in FundingApi.ts

## Question
waitForCompletion polls until status !== 'executing' and reports success for any other value; can an attacker cause FundingApi.moonpay to report success for a failed, refunded or cancelled order?

## Target
- File/function: [src/client/funding/FundingApi.ts](src/client/funding/FundingApi.ts) - FundingApi.moonpay, FundingApi.coinbase
- Entrypoint: privy.funding.*
- Attacker controls: which on-ramp is selected and the input object forwarded to it
- Exploit idea: Return a terminal status other than success and inspect the mapped result.
- Invariant to test: Only an explicit success status may be reported as success.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: enumerate terminal statuses through FundingApi.moonpay and assert only success maps to success.
