# Q2509: sandbox flag selects the endpoint in MoonpayOnRampApi.ts

## Question
getTransactionStatus picks the sandbox or prod key from a boolean; can an attacker flip that flag through MoonpayOnRampApi.sign (MoonpayOnRampSign) so a sandbox transaction is presented to the user as a real one?

## Target
- File/function: [src/client/funding/MoonpayOnRampApi.ts](src/client/funding/MoonpayOnRampApi.ts) - MoonpayOnRampApi.sign (MoonpayOnRampSign), getTransactionStatus (direct api.moonpay.com fetch with embedded pk_live key)
- Entrypoint: privy.funding.moonpay.sign(input) / getTransactionStatus({transactionId, useSandbox})
- Attacker controls: the sign input body (walletAddress, currency, amount) and transactionId
- Exploit idea: Call the status path with useSandbox toggled and inspect what the app reports.
- Invariant to test: Environment selection must be pinned by configuration, not per call.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert MoonpayOnRampApi.sign (MoonpayOnRampSign) derives the environment from configuration.
