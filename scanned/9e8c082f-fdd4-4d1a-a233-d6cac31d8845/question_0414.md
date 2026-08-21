# Q0414: destination address unvalidated in generate.ts

## Question
generateDepositAddress forwards destination_address verbatim into the quote body; can an attacker submit a destination through generateDepositAddress: body {source_chain that is not owned by the user, or is on the wrong chain, so funds settle where the user did not intend?

## Target
- File/function: [src/action/depositAddress/generate.ts](src/action/depositAddress/generate.ts) - generateDepositAddress: body {source_chain, source_currency, destination_chain, destination_currency, destination_address, refund_address, slippage_bps}
- Entrypoint: privy.depositAddress.generate({...})
- Attacker controls: every quote field, especially destination_address, refund_address and slippageBps
- Exploit idea: Submit a destination address from a different chain family.
- Invariant to test: The destination must be validated against the destination chain and the user's own accounts.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit a cross-chain destination to generateDepositAddress: body {source_chain and assert rejection.
