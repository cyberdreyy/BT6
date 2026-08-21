# Q0969: completion decided by a status string in MoonpayOnRampApi.ts

## Question
waitForCompletion polls until status !== 'executing' and reports success for any other value; can an attacker cause MoonpayOnRampApi.sign (MoonpayOnRampSign) to report success for a failed, refunded or cancelled order?

## Target
- File/function: [src/client/funding/MoonpayOnRampApi.ts](src/client/funding/MoonpayOnRampApi.ts) - MoonpayOnRampApi.sign (MoonpayOnRampSign), getTransactionStatus (direct api.moonpay.com fetch with embedded pk_live key)
- Entrypoint: privy.funding.moonpay.sign(input) / getTransactionStatus({transactionId, useSandbox})
- Attacker controls: the sign input body (walletAddress, currency, amount) and transactionId
- Exploit idea: Return a terminal status other than success and inspect the mapped result.
- Invariant to test: Only an explicit success status may be reported as success.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: enumerate terminal statuses through MoonpayOnRampApi.sign (MoonpayOnRampSign) and assert only success maps to success.
