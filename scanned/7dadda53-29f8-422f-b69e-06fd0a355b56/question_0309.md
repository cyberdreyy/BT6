# Q0309: caip2 prefix matching is loose in MoonpayOnRampApi.ts

## Question
caip2ToChainType matches on 'eip155:', 'solana:', 'bip122:' and 'tron:' prefixes only; can an attacker pass a caip2 string through MoonpayOnRampApi.sign (MoonpayOnRampSign) whose prefix matches one chain family while the numeric reference points at another chain?

## Target
- File/function: [src/client/funding/MoonpayOnRampApi.ts](src/client/funding/MoonpayOnRampApi.ts) - MoonpayOnRampApi.sign (MoonpayOnRampSign), getTransactionStatus (direct api.moonpay.com fetch with embedded pk_live key)
- Entrypoint: privy.funding.moonpay.sign(input) / getTransactionStatus({transactionId, useSandbox})
- Attacker controls: the sign input body (walletAddress, currency, amount) and transactionId
- Exploit idea: Pass 'eip155:999999' and observe the chain type and address chosen.
- Invariant to test: Chain identity must be resolved from the full caip2 reference, not the prefix.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: table-test caip2 strings through MoonpayOnRampApi.sign (MoonpayOnRampSign) and assert full-reference validation.
