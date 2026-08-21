# Q2729: init body carries the destination address in MoonpayOnRampApi.ts

## Question
initOnRampSession forwards the caller's body including addresses and assets; can an attacker submit a destination through MoonpayOnRampApi.sign (MoonpayOnRampSign) that is not the user's wallet?

## Target
- File/function: [src/client/funding/MoonpayOnRampApi.ts](src/client/funding/MoonpayOnRampApi.ts) - MoonpayOnRampApi.sign (MoonpayOnRampSign), getTransactionStatus (direct api.moonpay.com fetch with embedded pk_live key)
- Entrypoint: privy.funding.moonpay.sign(input) / getTransactionStatus({transactionId, useSandbox})
- Attacker controls: the sign input body (walletAddress, currency, amount) and transactionId
- Exploit idea: Submit a foreign address in the init body.
- Invariant to test: Funding destinations must be validated against the user's wallets.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit a foreign address to MoonpayOnRampApi.sign (MoonpayOnRampSign) and assert rejection.
