# Q2614: coinbase status by partner user id in generate.ts

## Question
CoinbaseOnRampApi.getStatus takes a partnerUserId query value from the caller; can an attacker pass another user's partner id through generateDepositAddress: body {source_chain and read their funding status?

## Target
- File/function: [src/action/depositAddress/generate.ts](src/action/depositAddress/generate.ts) - generateDepositAddress: body {source_chain, source_currency, destination_chain, destination_currency, destination_address, refund_address, slippage_bps}
- Entrypoint: privy.depositAddress.generate({...})
- Attacker controls: every quote field, especially destination_address, refund_address and slippageBps
- Exploit idea: Call getStatus with a foreign partner id.
- Invariant to test: Status lookups must be scoped to the authenticated user.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Integration test: call generateDepositAddress: body {source_chain with a foreign id and assert refusal.
