# Q1520: on-ramp url built from server values in CoinbaseOnRampApi.ts

## Question
getCoinbaseOnRampUrl embeds sessionToken, partnerUserId and appId from the init response into pay.coinbase.com query parameters; can an attacker influence the init response so CoinbaseOnRampApi.initOnRampSession produces a URL that funds a different partner user?

## Target
- File/function: [src/client/funding/CoinbaseOnRampApi.ts](src/client/funding/CoinbaseOnRampApi.ts) - CoinbaseOnRampApi.initOnRampSession, getStatus(partnerUserId)
- Entrypoint: privy.funding.coinbase.initOnRampSession(input)
- Attacker controls: the init body (addresses, assets, amount) and partnerUserId query value
- Exploit idea: Return an init response with a foreign partner_user_id and inspect the URL.
- Invariant to test: On-ramp URL parameters must be bound to the authenticated user's session.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return a foreign partner id and assert CoinbaseOnRampApi.initOnRampSession refuses to build the URL.
