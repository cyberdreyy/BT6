# Q1409: abort signal supplied by the caller in MoonpayOnRampApi.ts

## Question
poll checks a caller-supplied AbortSignal; can an attacker abort MoonpayOnRampApi.sign (MoonpayOnRampSign) at a chosen moment so the app treats a completed deposit as aborted and issues a duplicate?

## Target
- File/function: [src/client/funding/MoonpayOnRampApi.ts](src/client/funding/MoonpayOnRampApi.ts) - MoonpayOnRampApi.sign (MoonpayOnRampSign), getTransactionStatus (direct api.moonpay.com fetch with embedded pk_live key)
- Entrypoint: privy.funding.moonpay.sign(input) / getTransactionStatus({transactionId, useSandbox})
- Attacker controls: the sign input body (walletAddress, currency, amount) and transactionId
- Exploit idea: Abort right after the funds land.
- Invariant to test: Abort must not change the recorded outcome of a settled deposit.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Integration test: abort MoonpayOnRampApi.sign (MoonpayOnRampSign) after settlement and assert the state reflects settlement.
