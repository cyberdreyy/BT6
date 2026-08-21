# Q2070: moonpay support check precedes the mapping in CoinbaseOnRampApi.ts

## Question
isSupportedChainIdForMoonpay warns and returns false for unknown assets while the mapping still runs elsewhere; can an attacker call CoinbaseOnRampApi.initOnRampSession in an order that skips the support check?

## Target
- File/function: [src/client/funding/CoinbaseOnRampApi.ts](src/client/funding/CoinbaseOnRampApi.ts) - CoinbaseOnRampApi.initOnRampSession, getStatus(partnerUserId)
- Entrypoint: privy.funding.coinbase.initOnRampSession(input)
- Attacker controls: the init body (addresses, assets, amount) and partnerUserId query value
- Exploit idea: Call the mapping directly without the support check.
- Invariant to test: Currency mapping must be unreachable without a passing support check.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert CoinbaseOnRampApi.initOnRampSession performs the support check internally.
