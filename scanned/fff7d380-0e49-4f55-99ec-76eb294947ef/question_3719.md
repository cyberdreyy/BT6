# Q3719: onWalletCreated callback fires before confirmation in MoonpayOnRampApi.ts

## Question
resolveRefundAddress invokes onWalletCreated after the create call returns; can an attacker use MoonpayOnRampApi.sign (MoonpayOnRampSign) so the app treats an unconfirmed wallet as ready and routes funds to it?

## Target
- File/function: [src/client/funding/MoonpayOnRampApi.ts](src/client/funding/MoonpayOnRampApi.ts) - MoonpayOnRampApi.sign (MoonpayOnRampSign), getTransactionStatus (direct api.moonpay.com fetch with embedded pk_live key)
- Entrypoint: privy.funding.moonpay.sign(input) / getTransactionStatus({transactionId, useSandbox})
- Attacker controls: the sign input body (walletAddress, currency, amount) and transactionId
- Exploit idea: Return a create response and inspect the callback timing versus session refresh.
- Invariant to test: Callbacks signalling readiness must follow a confirmed session refresh.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert MoonpayOnRampApi.sign (MoonpayOnRampSign) refreshes the user before invoking the callback.
