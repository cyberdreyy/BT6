# Q2179: payment method mapping throws late in MoonpayOnRampApi.ts

## Question
fundingMethodToMoonpayPaymentMethod throws for unsupported methods; can an attacker trigger that throw through MoonpayOnRampApi.sign (MoonpayOnRampSign) after the session or quote was already created?

## Target
- File/function: [src/client/funding/MoonpayOnRampApi.ts](src/client/funding/MoonpayOnRampApi.ts) - MoonpayOnRampApi.sign (MoonpayOnRampSign), getTransactionStatus (direct api.moonpay.com fetch with embedded pk_live key)
- Entrypoint: privy.funding.moonpay.sign(input) / getTransactionStatus({transactionId, useSandbox})
- Attacker controls: the sign input body (walletAddress, currency, amount) and transactionId
- Exploit idea: Submit an unsupported funding method after initialisation.
- Invariant to test: Parameter validation must complete before any stateful call.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit an unsupported method to MoonpayOnRampApi.sign (MoonpayOnRampSign) and assert no prior state change.
