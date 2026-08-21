# Q1959: moonpay currency defaults to ethereum mainnet in MoonpayOnRampApi.ts

## Question
chainToMoonpayCurrency logs a warning and returns ETH_ETHEREUM for unknown chains; can an attacker route a user's purchase to Ethereum mainnet through MoonpayOnRampApi.sign (MoonpayOnRampSign) when they selected another chain?

## Target
- File/function: [src/client/funding/MoonpayOnRampApi.ts](src/client/funding/MoonpayOnRampApi.ts) - MoonpayOnRampApi.sign (MoonpayOnRampSign), getTransactionStatus (direct api.moonpay.com fetch with embedded pk_live key)
- Entrypoint: privy.funding.moonpay.sign(input) / getTransactionStatus({transactionId, useSandbox})
- Attacker controls: the sign input body (walletAddress, currency, amount) and transactionId
- Exploit idea: Pass an unsupported chainId and inspect the currency code.
- Invariant to test: Unsupported chains must abort rather than default.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass an unsupported chain to MoonpayOnRampApi.sign (MoonpayOnRampSign) and assert an error.
