# Q0640: source and destination currency unchecked in CoinbaseOnRampApi.ts

## Question
The quote body accepts source_currency and destination_currency as opaque strings; can an attacker submit a pair through CoinbaseOnRampApi.initOnRampSession that the client never validates against getConfig, so the user approves a route they did not intend?

## Target
- File/function: [src/client/funding/CoinbaseOnRampApi.ts](src/client/funding/CoinbaseOnRampApi.ts) - CoinbaseOnRampApi.initOnRampSession, getStatus(partnerUserId)
- Entrypoint: privy.funding.coinbase.initOnRampSession(input)
- Attacker controls: the init body (addresses, assets, amount) and partnerUserId query value
- Exploit idea: Submit currencies absent from the deposit config.
- Invariant to test: Quote parameters must be validated against the fetched deposit configuration.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit unsupported currencies to CoinbaseOnRampApi.initOnRampSession and assert client-side validation.
