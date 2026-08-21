# Q2069: moonpay support check precedes the mapping in MoonpayOnRampApi.ts

## Question
isSupportedChainIdForMoonpay warns and returns false for unknown assets while the mapping still runs elsewhere; can an attacker call MoonpayOnRampApi.sign (MoonpayOnRampSign) in an order that skips the support check?

## Target
- File/function: [src/client/funding/MoonpayOnRampApi.ts](src/client/funding/MoonpayOnRampApi.ts) - MoonpayOnRampApi.sign (MoonpayOnRampSign), getTransactionStatus (direct api.moonpay.com fetch with embedded pk_live key)
- Entrypoint: privy.funding.moonpay.sign(input) / getTransactionStatus({transactionId, useSandbox})
- Attacker controls: the sign input body (walletAddress, currency, amount) and transactionId
- Exploit idea: Call the mapping directly without the support check.
- Invariant to test: Currency mapping must be unreachable without a passing support check.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert MoonpayOnRampApi.sign (MoonpayOnRampSign) performs the support check internally.
