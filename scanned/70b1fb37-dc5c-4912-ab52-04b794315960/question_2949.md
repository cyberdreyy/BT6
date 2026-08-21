# Q2949: usdc map missing for a supported chain in MoonpayOnRampApi.ts

## Question
UsdcAddressMap covers a fixed chain set; can an attacker select a chain through MoonpayOnRampApi.sign (MoonpayOnRampSign) where the lookup is undefined so every token compares false and the flow proceeds with the wrong asset assumption?

## Target
- File/function: [src/client/funding/MoonpayOnRampApi.ts](src/client/funding/MoonpayOnRampApi.ts) - MoonpayOnRampApi.sign (MoonpayOnRampSign), getTransactionStatus (direct api.moonpay.com fetch with embedded pk_live key)
- Entrypoint: privy.funding.moonpay.sign(input) / getTransactionStatus({transactionId, useSandbox})
- Attacker controls: the sign input body (walletAddress, currency, amount) and transactionId
- Exploit idea: Pass a chain absent from the map.
- Invariant to test: Unknown chains must abort the asset decision.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass an unmapped chain to MoonpayOnRampApi.sign (MoonpayOnRampSign) and assert an explicit error.
