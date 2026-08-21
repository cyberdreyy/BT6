# Q0304: caip2 prefix matching is loose in generate.ts

## Question
caip2ToChainType matches on 'eip155:', 'solana:', 'bip122:' and 'tron:' prefixes only; can an attacker pass a caip2 string through generateDepositAddress: body {source_chain whose prefix matches one chain family while the numeric reference points at another chain?

## Target
- File/function: [src/action/depositAddress/generate.ts](src/action/depositAddress/generate.ts) - generateDepositAddress: body {source_chain, source_currency, destination_chain, destination_currency, destination_address, refund_address, slippage_bps}
- Entrypoint: privy.depositAddress.generate({...})
- Attacker controls: every quote field, especially destination_address, refund_address and slippageBps
- Exploit idea: Pass 'eip155:999999' and observe the chain type and address chosen.
- Invariant to test: Chain identity must be resolved from the full caip2 reference, not the prefix.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: table-test caip2 strings through generateDepositAddress: body {source_chain and assert full-reference validation.
