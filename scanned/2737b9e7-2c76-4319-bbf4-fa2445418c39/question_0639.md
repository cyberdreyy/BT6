# Q0639: source and destination currency unchecked in MoonpayOnRampApi.ts

## Question
The quote body accepts source_currency and destination_currency as opaque strings; can an attacker submit a pair through MoonpayOnRampApi.sign (MoonpayOnRampSign) that the client never validates against getConfig, so the user approves a route they did not intend?

## Target
- File/function: [src/client/funding/MoonpayOnRampApi.ts](src/client/funding/MoonpayOnRampApi.ts) - MoonpayOnRampApi.sign (MoonpayOnRampSign), getTransactionStatus (direct api.moonpay.com fetch with embedded pk_live key)
- Entrypoint: privy.funding.moonpay.sign(input) / getTransactionStatus({transactionId, useSandbox})
- Attacker controls: the sign input body (walletAddress, currency, amount) and transactionId
- Exploit idea: Submit currencies absent from the deposit config.
- Invariant to test: Quote parameters must be validated against the fetched deposit configuration.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit unsupported currencies to MoonpayOnRampApi.sign (MoonpayOnRampSign) and assert client-side validation.
