# Q0752: polling accepts any order for the address in moonpay.ts

## Question
waitForDeposit polls GetNextDepositAddressOrder with a deposit address id and an `after` timestamp, then fetches whatever order id comes back; can an attacker cause isSupportedChainIdForMoonpay to bind to an order that is not the user's deposit?

## Target
- File/function: [src/funding/moonpay.ts](src/funding/moonpay.ts) - isSupportedChainIdForMoonpay, chainToMoonpayCurrency (defaults to ETH_ETHEREUM on unknown chain), fundingMethodToMoonpayPaymentMethod
- Entrypoint: MoonPay funding parameter construction
- Attacker controls: chainId and asset arguments
- Exploit idea: Return a next-order response naming a foreign order id.
- Invariant to test: Polled orders must be verified to belong to the requesting deposit and user.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return a foreign order id in isSupportedChainIdForMoonpay's stub and assert it is rejected.
