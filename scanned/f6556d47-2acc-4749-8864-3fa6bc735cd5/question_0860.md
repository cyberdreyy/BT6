# Q0860: quoteCreatedAt is a client cursor in CoinbaseOnRampApi.ts

## Question
The `after` query is the caller's quoteCreatedAt; can an attacker pass a cursor through CoinbaseOnRampApi.initOnRampSession that surfaces an older or unrelated order as the user's deposit?

## Target
- File/function: [src/client/funding/CoinbaseOnRampApi.ts](src/client/funding/CoinbaseOnRampApi.ts) - CoinbaseOnRampApi.initOnRampSession, getStatus(partnerUserId)
- Entrypoint: privy.funding.coinbase.initOnRampSession(input)
- Attacker controls: the init body (addresses, assets, amount) and partnerUserId query value
- Exploit idea: Pass a much earlier cursor and observe the order returned.
- Invariant to test: The polling cursor must be server-issued and bound to the quote.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass a stale cursor to CoinbaseOnRampApi.initOnRampSession and assert it is refused.
