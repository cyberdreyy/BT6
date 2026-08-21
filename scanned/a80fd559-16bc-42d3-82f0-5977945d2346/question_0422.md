# Q0422: destination address unvalidated in moonpay.ts

## Question
generateDepositAddress forwards destination_address verbatim into the quote body; can an attacker submit a destination through isSupportedChainIdForMoonpay that is not owned by the user, or is on the wrong chain, so funds settle where the user did not intend?

## Target
- File/function: [src/funding/moonpay.ts](src/funding/moonpay.ts) - isSupportedChainIdForMoonpay, chainToMoonpayCurrency (defaults to ETH_ETHEREUM on unknown chain), fundingMethodToMoonpayPaymentMethod
- Entrypoint: MoonPay funding parameter construction
- Attacker controls: chainId and asset arguments
- Exploit idea: Submit a destination address from a different chain family.
- Invariant to test: The destination must be validated against the destination chain and the user's own accounts.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit a cross-chain destination to isSupportedChainIdForMoonpay and assert rejection.
