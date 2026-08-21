# Q1299: attempt arithmetic derived from the interval in MoonpayOnRampApi.ts

## Question
The attempt count is ceil(timeout/interval) with a caller-supplied interval; can an attacker pass a tiny interval through MoonpayOnRampApi.sign (MoonpayOnRampSign) to multiply requests, or a huge one so the deposit is never observed?

## Target
- File/function: [src/client/funding/MoonpayOnRampApi.ts](src/client/funding/MoonpayOnRampApi.ts) - MoonpayOnRampApi.sign (MoonpayOnRampSign), getTransactionStatus (direct api.moonpay.com fetch with embedded pk_live key)
- Entrypoint: privy.funding.moonpay.sign(input) / getTransactionStatus({transactionId, useSandbox})
- Attacker controls: the sign input body (walletAddress, currency, amount) and transactionId
- Exploit idea: Pass extreme pollIntervalMs values.
- Invariant to test: Polling parameters must be bounded by the SDK.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass extreme intervals to MoonpayOnRampApi.sign (MoonpayOnRampSign) and assert clamping.
