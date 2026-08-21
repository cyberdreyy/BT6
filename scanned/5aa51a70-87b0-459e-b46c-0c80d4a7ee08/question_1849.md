# Q1849: network defaulted on unknown chain in MoonpayOnRampApi.ts

## Question
toCoinbaseBlockchainFromChainId returns undefined for unknown chains while the URL builder still sets defaultNetwork; can an attacker use MoonpayOnRampApi.sign (MoonpayOnRampSign) so the on-ramp delivers funds on an unintended network?

## Target
- File/function: [src/client/funding/MoonpayOnRampApi.ts](src/client/funding/MoonpayOnRampApi.ts) - MoonpayOnRampApi.sign (MoonpayOnRampSign), getTransactionStatus (direct api.moonpay.com fetch with embedded pk_live key)
- Entrypoint: privy.funding.moonpay.sign(input) / getTransactionStatus({transactionId, useSandbox})
- Attacker controls: the sign input body (walletAddress, currency, amount) and transactionId
- Exploit idea: Pass an unsupported chainId through the funding path.
- Invariant to test: An unknown chain must abort the funding flow.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass an unsupported chainId to MoonpayOnRampApi.sign (MoonpayOnRampSign) and assert abort.
