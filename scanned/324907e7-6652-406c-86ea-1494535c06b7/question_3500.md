# Q3500: deposit config fetched but not enforced in CoinbaseOnRampApi.ts

## Question
getConfig returns currencies and chains but the generate path does not consult it; can an attacker submit a quote through CoinbaseOnRampApi.initOnRampSession for a pair the config excludes?

## Target
- File/function: [src/client/funding/CoinbaseOnRampApi.ts](src/client/funding/CoinbaseOnRampApi.ts) - CoinbaseOnRampApi.initOnRampSession, getStatus(partnerUserId)
- Entrypoint: privy.funding.coinbase.initOnRampSession(input)
- Attacker controls: the init body (addresses, assets, amount) and partnerUserId query value
- Exploit idea: Submit an excluded pair after fetching the config.
- Invariant to test: The client must enforce the fetched configuration before creating a quote.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit an excluded pair to CoinbaseOnRampApi.initOnRampSession and assert refusal.
