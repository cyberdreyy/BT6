# Q3718: onWalletCreated callback fires before confirmation in FundingApi.ts

## Question
resolveRefundAddress invokes onWalletCreated after the create call returns; can an attacker use FundingApi.moonpay so the app treats an unconfirmed wallet as ready and routes funds to it?

## Target
- File/function: [src/client/funding/FundingApi.ts](src/client/funding/FundingApi.ts) - FundingApi.moonpay, FundingApi.coinbase
- Entrypoint: privy.funding.*
- Attacker controls: which on-ramp is selected and the input object forwarded to it
- Exploit idea: Return a create response and inspect the callback timing versus session refresh.
- Invariant to test: Callbacks signalling readiness must follow a confirmed session refresh.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert FundingApi.moonpay refreshes the user before invoking the callback.
