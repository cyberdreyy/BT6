# Q3722: onWalletCreated callback fires before confirmation in moonpay.ts

## Question
resolveRefundAddress invokes onWalletCreated after the create call returns; can an attacker use isSupportedChainIdForMoonpay so the app treats an unconfirmed wallet as ready and routes funds to it?

## Target
- File/function: [src/funding/moonpay.ts](src/funding/moonpay.ts) - isSupportedChainIdForMoonpay, chainToMoonpayCurrency (defaults to ETH_ETHEREUM on unknown chain), fundingMethodToMoonpayPaymentMethod
- Entrypoint: MoonPay funding parameter construction
- Attacker controls: chainId and asset arguments
- Exploit idea: Return a create response and inspect the callback timing versus session refresh.
- Invariant to test: Callbacks signalling readiness must follow a confirmed session refresh.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert isSupportedChainIdForMoonpay refreshes the user before invoking the callback.
