# Q0859: quoteCreatedAt is a client cursor in MoonpayOnRampApi.ts

## Question
The `after` query is the caller's quoteCreatedAt; can an attacker pass a cursor through MoonpayOnRampApi.sign (MoonpayOnRampSign) that surfaces an older or unrelated order as the user's deposit?

## Target
- File/function: [src/client/funding/MoonpayOnRampApi.ts](src/client/funding/MoonpayOnRampApi.ts) - MoonpayOnRampApi.sign (MoonpayOnRampSign), getTransactionStatus (direct api.moonpay.com fetch with embedded pk_live key)
- Entrypoint: privy.funding.moonpay.sign(input) / getTransactionStatus({transactionId, useSandbox})
- Attacker controls: the sign input body (walletAddress, currency, amount) and transactionId
- Exploit idea: Pass a much earlier cursor and observe the order returned.
- Invariant to test: The polling cursor must be server-issued and bound to the quote.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass a stale cursor to MoonpayOnRampApi.sign (MoonpayOnRampSign) and assert it is refused.
