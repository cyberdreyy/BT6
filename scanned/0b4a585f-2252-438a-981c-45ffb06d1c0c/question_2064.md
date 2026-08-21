# Q2064: moonpay support check precedes the mapping in generate.ts

## Question
isSupportedChainIdForMoonpay warns and returns false for unknown assets while the mapping still runs elsewhere; can an attacker call generateDepositAddress: body {source_chain in an order that skips the support check?

## Target
- File/function: [src/action/depositAddress/generate.ts](src/action/depositAddress/generate.ts) - generateDepositAddress: body {source_chain, source_currency, destination_chain, destination_currency, destination_address, refund_address, slippage_bps}
- Entrypoint: privy.depositAddress.generate({...})
- Attacker controls: every quote field, especially destination_address, refund_address and slippageBps
- Exploit idea: Call the mapping directly without the support check.
- Invariant to test: Currency mapping must be unreachable without a passing support check.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert generateDepositAddress: body {source_chain performs the support check internally.
