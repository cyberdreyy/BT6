# Q0420: destination address unvalidated in CoinbaseOnRampApi.ts

## Question
generateDepositAddress forwards destination_address verbatim into the quote body; can an attacker submit a destination through CoinbaseOnRampApi.initOnRampSession that is not owned by the user, or is on the wrong chain, so funds settle where the user did not intend?

## Target
- File/function: [src/client/funding/CoinbaseOnRampApi.ts](src/client/funding/CoinbaseOnRampApi.ts) - CoinbaseOnRampApi.initOnRampSession, getStatus(partnerUserId)
- Entrypoint: privy.funding.coinbase.initOnRampSession(input)
- Attacker controls: the init body (addresses, assets, amount) and partnerUserId query value
- Exploit idea: Submit a destination address from a different chain family.
- Invariant to test: The destination must be validated against the destination chain and the user's own accounts.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit a cross-chain destination to CoinbaseOnRampApi.initOnRampSession and assert rejection.
