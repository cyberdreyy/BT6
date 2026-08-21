# Q2289: moonpay sign input forwarded verbatim in MoonpayOnRampApi.ts

## Question
MoonpayOnRampApi.sign posts the caller's input body to the signing route; can an attacker include a walletAddress in MoonpayOnRampApi.sign (MoonpayOnRampSign) that is not theirs so the signed on-ramp URL delivers funds elsewhere?

## Target
- File/function: [src/client/funding/MoonpayOnRampApi.ts](src/client/funding/MoonpayOnRampApi.ts) - MoonpayOnRampApi.sign (MoonpayOnRampSign), getTransactionStatus (direct api.moonpay.com fetch with embedded pk_live key)
- Entrypoint: privy.funding.moonpay.sign(input) / getTransactionStatus({transactionId, useSandbox})
- Attacker controls: the sign input body (walletAddress, currency, amount) and transactionId
- Exploit idea: Submit a foreign wallet address in the sign input.
- Invariant to test: The funded address must be validated against the authenticated user's wallets.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit a foreign address to MoonpayOnRampApi.sign (MoonpayOnRampSign) and assert rejection.
