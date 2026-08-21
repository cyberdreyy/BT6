# Q1189: poll swallows every operation error in MoonpayOnRampApi.ts

## Question
poll catches all errors, records the last one and keeps iterating; can an attacker cause repeated authorization failures inside MoonpayOnRampApi.sign (MoonpayOnRampSign) to be hidden until max_attempts, so the app keeps polling with a stale session?

## Target
- File/function: [src/client/funding/MoonpayOnRampApi.ts](src/client/funding/MoonpayOnRampApi.ts) - MoonpayOnRampApi.sign (MoonpayOnRampSign), getTransactionStatus (direct api.moonpay.com fetch with embedded pk_live key)
- Entrypoint: privy.funding.moonpay.sign(input) / getTransactionStatus({transactionId, useSandbox})
- Attacker controls: the sign input body (walletAddress, currency, amount) and transactionId
- Exploit idea: Return 401s from the polled route and observe the loop behaviour.
- Invariant to test: Authorization failures must terminate polling immediately.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return 401 from MoonpayOnRampApi.sign (MoonpayOnRampSign)'s operation and assert immediate termination.
