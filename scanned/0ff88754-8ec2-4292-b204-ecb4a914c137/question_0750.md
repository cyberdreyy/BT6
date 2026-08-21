# Q0750: polling accepts any order for the address in CoinbaseOnRampApi.ts

## Question
waitForDeposit polls GetNextDepositAddressOrder with a deposit address id and an `after` timestamp, then fetches whatever order id comes back; can an attacker cause CoinbaseOnRampApi.initOnRampSession to bind to an order that is not the user's deposit?

## Target
- File/function: [src/client/funding/CoinbaseOnRampApi.ts](src/client/funding/CoinbaseOnRampApi.ts) - CoinbaseOnRampApi.initOnRampSession, getStatus(partnerUserId)
- Entrypoint: privy.funding.coinbase.initOnRampSession(input)
- Attacker controls: the init body (addresses, assets, amount) and partnerUserId query value
- Exploit idea: Return a next-order response naming a foreign order id.
- Invariant to test: Polled orders must be verified to belong to the requesting deposit and user.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return a foreign order id in CoinbaseOnRampApi.initOnRampSession's stub and assert it is rejected.
