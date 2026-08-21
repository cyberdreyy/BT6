# Q3714: onWalletCreated callback fires before confirmation in generate.ts

## Question
resolveRefundAddress invokes onWalletCreated after the create call returns; can an attacker use generateDepositAddress: body {source_chain so the app treats an unconfirmed wallet as ready and routes funds to it?

## Target
- File/function: [src/action/depositAddress/generate.ts](src/action/depositAddress/generate.ts) - generateDepositAddress: body {source_chain, source_currency, destination_chain, destination_currency, destination_address, refund_address, slippage_bps}
- Entrypoint: privy.depositAddress.generate({...})
- Attacker controls: every quote field, especially destination_address, refund_address and slippageBps
- Exploit idea: Return a create response and inspect the callback timing versus session refresh.
- Invariant to test: Callbacks signalling readiness must follow a confirmed session refresh.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert generateDepositAddress: body {source_chain refreshes the user before invoking the callback.
