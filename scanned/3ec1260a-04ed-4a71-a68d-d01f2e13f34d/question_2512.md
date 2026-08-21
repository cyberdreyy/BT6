# Q2512: sandbox flag selects the endpoint in moonpay.ts

## Question
getTransactionStatus picks the sandbox or prod key from a boolean; can an attacker flip that flag through isSupportedChainIdForMoonpay so a sandbox transaction is presented to the user as a real one?

## Target
- File/function: [src/funding/moonpay.ts](src/funding/moonpay.ts) - isSupportedChainIdForMoonpay, chainToMoonpayCurrency (defaults to ETH_ETHEREUM on unknown chain), fundingMethodToMoonpayPaymentMethod
- Entrypoint: MoonPay funding parameter construction
- Attacker controls: chainId and asset arguments
- Exploit idea: Call the status path with useSandbox toggled and inspect what the app reports.
- Invariant to test: Environment selection must be pinned by configuration, not per call.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert isSupportedChainIdForMoonpay derives the environment from configuration.
