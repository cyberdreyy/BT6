# Q1630: amount formatting patches leading dots in CoinbaseOnRampApi.ts

## Question
The amount helper rewrites a leading '.' to '0.' and otherwise passes the string through; can an attacker pass an amount through CoinbaseOnRampApi.initOnRampSession (exponential, thousands separators, trailing characters) that the on-ramp parses differently than the app displayed?

## Target
- File/function: [src/client/funding/CoinbaseOnRampApi.ts](src/client/funding/CoinbaseOnRampApi.ts) - CoinbaseOnRampApi.initOnRampSession, getStatus(partnerUserId)
- Entrypoint: privy.funding.coinbase.initOnRampSession(input)
- Attacker controls: the init body (addresses, assets, amount) and partnerUserId query value
- Exploit idea: Pass '1e3', '1,000' and '1.0abc' and inspect the URL value.
- Invariant to test: Amounts must be canonicalised and validated before they leave the SDK.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: table-test amount strings through CoinbaseOnRampApi.initOnRampSession.
