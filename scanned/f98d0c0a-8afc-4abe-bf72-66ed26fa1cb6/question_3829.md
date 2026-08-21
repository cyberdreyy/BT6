# Q3829: not-authenticated returned as a soft error in MoonpayOnRampApi.ts

## Question
resolveRefundAddress returns {ok:false, error:'NOT_AUTHENTICATED'} rather than throwing; can an attacker exploit that soft failure in MoonpayOnRampApi.sign (MoonpayOnRampSign) so the caller proceeds with an undefined address?

## Target
- File/function: [src/client/funding/MoonpayOnRampApi.ts](src/client/funding/MoonpayOnRampApi.ts) - MoonpayOnRampApi.sign (MoonpayOnRampSign), getTransactionStatus (direct api.moonpay.com fetch with embedded pk_live key)
- Entrypoint: privy.funding.moonpay.sign(input) / getTransactionStatus({transactionId, useSandbox})
- Attacker controls: the sign input body (walletAddress, currency, amount) and transactionId
- Exploit idea: Call the flow with no session and follow the caller's handling.
- Invariant to test: Authentication failures must be unambiguous and terminal.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: call MoonpayOnRampApi.sign (MoonpayOnRampSign) unauthenticated and assert the caller cannot proceed.
