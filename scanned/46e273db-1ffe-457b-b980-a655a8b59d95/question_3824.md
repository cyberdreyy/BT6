# Q3824: not-authenticated returned as a soft error in generate.ts

## Question
resolveRefundAddress returns {ok:false, error:'NOT_AUTHENTICATED'} rather than throwing; can an attacker exploit that soft failure in generateDepositAddress: body {source_chain so the caller proceeds with an undefined address?

## Target
- File/function: [src/action/depositAddress/generate.ts](src/action/depositAddress/generate.ts) - generateDepositAddress: body {source_chain, source_currency, destination_chain, destination_currency, destination_address, refund_address, slippage_bps}
- Entrypoint: privy.depositAddress.generate({...})
- Attacker controls: every quote field, especially destination_address, refund_address and slippageBps
- Exploit idea: Call the flow with no session and follow the caller's handling.
- Invariant to test: Authentication failures must be unambiguous and terminal.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: call generateDepositAddress: body {source_chain unauthenticated and assert the caller cannot proceed.
