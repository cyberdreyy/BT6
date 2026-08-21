# Q0089: refund address picked by chain-type scan in MoonpayOnRampApi.ts

## Question
resolveRefundAddress maps the caip2 string to a chain type and then takes the FIRST linked_account of that chain type; can an unprivileged attacker cause an externally linked or attacker-influenced wallet to occupy that position so MoonpayOnRampApi.sign (MoonpayOnRampSign) sets it as the refund address for the victim's deposit?

## Target
- File/function: [src/client/funding/MoonpayOnRampApi.ts](src/client/funding/MoonpayOnRampApi.ts) - MoonpayOnRampApi.sign (MoonpayOnRampSign), getTransactionStatus (direct api.moonpay.com fetch with embedded pk_live key)
- Entrypoint: privy.funding.moonpay.sign(input) / getTransactionStatus({transactionId, useSandbox})
- Attacker controls: the sign input body (walletAddress, currency, amount) and transactionId
- Exploit idea: Link an additional wallet of the same chain type and observe which address the refund resolution selects.
- Invariant to test: The refund address must be an embedded wallet the user explicitly selected, not the first matching linked account.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: build a user whose first matching linked account is an external wallet and assert MoonpayOnRampApi.sign (MoonpayOnRampSign) requires an explicit refund selection.
