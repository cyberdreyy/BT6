# Q0970: completion decided by a status string in CoinbaseOnRampApi.ts

## Question
waitForCompletion polls until status !== 'executing' and reports success for any other value; can an attacker cause CoinbaseOnRampApi.initOnRampSession to report success for a failed, refunded or cancelled order?

## Target
- File/function: [src/client/funding/CoinbaseOnRampApi.ts](src/client/funding/CoinbaseOnRampApi.ts) - CoinbaseOnRampApi.initOnRampSession, getStatus(partnerUserId)
- Entrypoint: privy.funding.coinbase.initOnRampSession(input)
- Attacker controls: the init body (addresses, assets, amount) and partnerUserId query value
- Exploit idea: Return a terminal status other than success and inspect the mapped result.
- Invariant to test: Only an explicit success status may be reported as success.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: enumerate terminal statuses through CoinbaseOnRampApi.initOnRampSession and assert only success maps to success.
