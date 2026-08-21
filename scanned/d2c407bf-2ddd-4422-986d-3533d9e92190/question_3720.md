# Q3720: onWalletCreated callback fires before confirmation in CoinbaseOnRampApi.ts

## Question
resolveRefundAddress invokes onWalletCreated after the create call returns; can an attacker use CoinbaseOnRampApi.initOnRampSession so the app treats an unconfirmed wallet as ready and routes funds to it?

## Target
- File/function: [src/client/funding/CoinbaseOnRampApi.ts](src/client/funding/CoinbaseOnRampApi.ts) - CoinbaseOnRampApi.initOnRampSession, getStatus(partnerUserId)
- Entrypoint: privy.funding.coinbase.initOnRampSession(input)
- Attacker controls: the init body (addresses, assets, amount) and partnerUserId query value
- Exploit idea: Return a create response and inspect the callback timing versus session refresh.
- Invariant to test: Callbacks signalling readiness must follow a confirmed session refresh.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert CoinbaseOnRampApi.initOnRampSession refreshes the user before invoking the callback.
