# Q0634: source and destination currency unchecked in generate.ts

## Question
The quote body accepts source_currency and destination_currency as opaque strings; can an attacker submit a pair through generateDepositAddress: body {source_chain that the client never validates against getConfig, so the user approves a route they did not intend?

## Target
- File/function: [src/action/depositAddress/generate.ts](src/action/depositAddress/generate.ts) - generateDepositAddress: body {source_chain, source_currency, destination_chain, destination_currency, destination_address, refund_address, slippage_bps}
- Entrypoint: privy.depositAddress.generate({...})
- Attacker controls: every quote field, especially destination_address, refund_address and slippageBps
- Exploit idea: Submit currencies absent from the deposit config.
- Invariant to test: Quote parameters must be validated against the fetched deposit configuration.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit unsupported currencies to generateDepositAddress: body {source_chain and assert client-side validation.
