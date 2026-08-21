# Q0199: refund falls back to creating a wallet in MoonpayOnRampApi.ts

## Question
When no matching account exists, resolveRefundAddress creates a wallet via the WalletCreate route and returns its address; can an attacker trigger that path through privy.funding.moonpay.sign(input) / getTransactionStatus({transactionId, useSandbox}) so a fresh wallet is provisioned and used as a refund sink without user confirmation?

## Target
- File/function: [src/client/funding/MoonpayOnRampApi.ts](src/client/funding/MoonpayOnRampApi.ts) - MoonpayOnRampApi.sign (MoonpayOnRampSign), getTransactionStatus (direct api.moonpay.com fetch with embedded pk_live key)
- Entrypoint: privy.funding.moonpay.sign(input) / getTransactionStatus({transactionId, useSandbox})
- Attacker controls: the sign input body (walletAddress, currency, amount) and transactionId
- Exploit idea: Call the deposit flow for a chain the user has no wallet on.
- Invariant to test: Automatic wallet creation must not silently become the refund destination.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Integration test: call MoonpayOnRampApi.sign (MoonpayOnRampSign) for an unlinked chain and assert an explicit confirmation is required.
