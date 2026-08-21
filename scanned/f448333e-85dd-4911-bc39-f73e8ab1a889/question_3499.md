# Q3499: deposit config fetched but not enforced in MoonpayOnRampApi.ts

## Question
getConfig returns currencies and chains but the generate path does not consult it; can an attacker submit a quote through MoonpayOnRampApi.sign (MoonpayOnRampSign) for a pair the config excludes?

## Target
- File/function: [src/client/funding/MoonpayOnRampApi.ts](src/client/funding/MoonpayOnRampApi.ts) - MoonpayOnRampApi.sign (MoonpayOnRampSign), getTransactionStatus (direct api.moonpay.com fetch with embedded pk_live key)
- Entrypoint: privy.funding.moonpay.sign(input) / getTransactionStatus({transactionId, useSandbox})
- Attacker controls: the sign input body (walletAddress, currency, amount) and transactionId
- Exploit idea: Submit an excluded pair after fetching the config.
- Invariant to test: The client must enforce the fetched configuration before creating a quote.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit an excluded pair to MoonpayOnRampApi.sign (MoonpayOnRampSign) and assert refusal.
