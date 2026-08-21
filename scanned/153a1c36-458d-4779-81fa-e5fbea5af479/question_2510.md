# Q2510: sandbox flag selects the endpoint in CoinbaseOnRampApi.ts

## Question
getTransactionStatus picks the sandbox or prod key from a boolean; can an attacker flip that flag through CoinbaseOnRampApi.initOnRampSession so a sandbox transaction is presented to the user as a real one?

## Target
- File/function: [src/client/funding/CoinbaseOnRampApi.ts](src/client/funding/CoinbaseOnRampApi.ts) - CoinbaseOnRampApi.initOnRampSession, getStatus(partnerUserId)
- Entrypoint: privy.funding.coinbase.initOnRampSession(input)
- Attacker controls: the init body (addresses, assets, amount) and partnerUserId query value
- Exploit idea: Call the status path with useSandbox toggled and inspect what the app reports.
- Invariant to test: Environment selection must be pinned by configuration, not per call.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert CoinbaseOnRampApi.initOnRampSession derives the environment from configuration.
