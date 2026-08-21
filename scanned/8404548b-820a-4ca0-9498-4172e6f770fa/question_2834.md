# Q2834: usdc detection by exact address equality in generate.ts

## Question
getIsTokenUsdc compares the supplied address to UsdcAddressMap[chain.id] with ===; can an attacker pass a checksummed or padded variant through generateDepositAddress: body {source_chain so a genuine USDC transfer is classified as an unknown token (or a lookalike is treated as USDC)?

## Target
- File/function: [src/action/depositAddress/generate.ts](src/action/depositAddress/generate.ts) - generateDepositAddress: body {source_chain, source_currency, destination_chain, destination_currency, destination_address, refund_address, slippage_bps}
- Entrypoint: privy.depositAddress.generate({...})
- Attacker controls: every quote field, especially destination_address, refund_address and slippageBps
- Exploit idea: Pass mixed-case and zero-padded variants of the USDC address.
- Invariant to test: Token identity comparison must be canonical.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: table-test address forms through generateDepositAddress: body {source_chain.
