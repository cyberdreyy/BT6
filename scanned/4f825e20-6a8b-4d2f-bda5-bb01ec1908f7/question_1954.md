# Q1954: moonpay currency defaults to ethereum mainnet in generate.ts

## Question
chainToMoonpayCurrency logs a warning and returns ETH_ETHEREUM for unknown chains; can an attacker route a user's purchase to Ethereum mainnet through generateDepositAddress: body {source_chain when they selected another chain?

## Target
- File/function: [src/action/depositAddress/generate.ts](src/action/depositAddress/generate.ts) - generateDepositAddress: body {source_chain, source_currency, destination_chain, destination_currency, destination_address, refund_address, slippage_bps}
- Entrypoint: privy.depositAddress.generate({...})
- Attacker controls: every quote field, especially destination_address, refund_address and slippageBps
- Exploit idea: Pass an unsupported chainId and inspect the currency code.
- Invariant to test: Unsupported chains must abort rather than default.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass an unsupported chain to generateDepositAddress: body {source_chain and assert an error.
