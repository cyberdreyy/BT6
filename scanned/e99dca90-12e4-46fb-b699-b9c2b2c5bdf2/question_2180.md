# Q2180: payment method mapping throws late in CoinbaseOnRampApi.ts

## Question
fundingMethodToMoonpayPaymentMethod throws for unsupported methods; can an attacker trigger that throw through CoinbaseOnRampApi.initOnRampSession after the session or quote was already created?

## Target
- File/function: [src/client/funding/CoinbaseOnRampApi.ts](src/client/funding/CoinbaseOnRampApi.ts) - CoinbaseOnRampApi.initOnRampSession, getStatus(partnerUserId)
- Entrypoint: privy.funding.coinbase.initOnRampSession(input)
- Attacker controls: the init body (addresses, assets, amount) and partnerUserId query value
- Exploit idea: Submit an unsupported funding method after initialisation.
- Invariant to test: Parameter validation must complete before any stateful call.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit an unsupported method to CoinbaseOnRampApi.initOnRampSession and assert no prior state change.
