# Q0419: destination address unvalidated in MoonpayOnRampApi.ts

## Question
generateDepositAddress forwards destination_address verbatim into the quote body; can an attacker submit a destination through MoonpayOnRampApi.sign (MoonpayOnRampSign) that is not owned by the user, or is on the wrong chain, so funds settle where the user did not intend?

## Target
- File/function: [src/client/funding/MoonpayOnRampApi.ts](src/client/funding/MoonpayOnRampApi.ts) - MoonpayOnRampApi.sign (MoonpayOnRampSign), getTransactionStatus (direct api.moonpay.com fetch with embedded pk_live key)
- Entrypoint: privy.funding.moonpay.sign(input) / getTransactionStatus({transactionId, useSandbox})
- Attacker controls: the sign input body (walletAddress, currency, amount) and transactionId
- Exploit idea: Submit a destination address from a different chain family.
- Invariant to test: The destination must be validated against the destination chain and the user's own accounts.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit a cross-chain destination to MoonpayOnRampApi.sign (MoonpayOnRampSign) and assert rejection.
