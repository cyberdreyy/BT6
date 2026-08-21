# Q0744: polling accepts any order for the address in generate.ts

## Question
waitForDeposit polls GetNextDepositAddressOrder with a deposit address id and an `after` timestamp, then fetches whatever order id comes back; can an attacker cause generateDepositAddress: body {source_chain to bind to an order that is not the user's deposit?

## Target
- File/function: [src/action/depositAddress/generate.ts](src/action/depositAddress/generate.ts) - generateDepositAddress: body {source_chain, source_currency, destination_chain, destination_currency, destination_address, refund_address, slippage_bps}
- Entrypoint: privy.depositAddress.generate({...})
- Attacker controls: every quote field, especially destination_address, refund_address and slippageBps
- Exploit idea: Return a next-order response naming a foreign order id.
- Invariant to test: Polled orders must be verified to belong to the requesting deposit and user.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return a foreign order id in generateDepositAddress: body {source_chain's stub and assert it is rejected.
