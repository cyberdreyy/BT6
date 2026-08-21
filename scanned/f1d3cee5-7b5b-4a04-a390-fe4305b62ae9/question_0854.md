# Q0854: quoteCreatedAt is a client cursor in generate.ts

## Question
The `after` query is the caller's quoteCreatedAt; can an attacker pass a cursor through generateDepositAddress: body {source_chain that surfaces an older or unrelated order as the user's deposit?

## Target
- File/function: [src/action/depositAddress/generate.ts](src/action/depositAddress/generate.ts) - generateDepositAddress: body {source_chain, source_currency, destination_chain, destination_currency, destination_address, refund_address, slippage_bps}
- Entrypoint: privy.depositAddress.generate({...})
- Attacker controls: every quote field, especially destination_address, refund_address and slippageBps
- Exploit idea: Pass a much earlier cursor and observe the order returned.
- Invariant to test: The polling cursor must be server-issued and bound to the quote.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass a stale cursor to generateDepositAddress: body {source_chain and assert it is refused.
