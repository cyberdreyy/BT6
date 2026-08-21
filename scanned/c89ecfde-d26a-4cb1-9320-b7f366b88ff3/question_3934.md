# Q3934: wallet creation failure hidden in generate.ts

## Question
The refund path returns REFUND_WALLET_CREATION_FAILED from a bare catch; can an attacker force that failure in generateDepositAddress: body {source_chain and have the deposit created with a missing or stale refund address?

## Target
- File/function: [src/action/depositAddress/generate.ts](src/action/depositAddress/generate.ts) - generateDepositAddress: body {source_chain, source_currency, destination_chain, destination_currency, destination_address, refund_address, slippage_bps}
- Entrypoint: privy.depositAddress.generate({...})
- Attacker controls: every quote field, especially destination_address, refund_address and slippageBps
- Exploit idea: Fail the create route and inspect the resulting quote body.
- Invariant to test: A deposit must not be created without a valid refund address.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: fail the create route and assert generateDepositAddress: body {source_chain aborts the quote.
