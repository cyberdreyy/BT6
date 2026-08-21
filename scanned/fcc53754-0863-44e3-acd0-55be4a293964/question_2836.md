# Q2836: usdc detection by exact address equality in polling.ts

## Question
getIsTokenUsdc compares the supplied address to UsdcAddressMap[chain.id] with ===; can an attacker pass a checksummed or padded variant through getDeposit so a genuine USDC transfer is classified as an unknown token (or a lookalike is treated as USDC)?

## Target
- File/function: [src/action/depositAddress/polling.ts](src/action/depositAddress/polling.ts) - getDeposit, waitForDeposit (GetNextDepositAddressOrder with after=quoteCreatedAt), waitForCompletion (until status !== 'executing'), 2000ms interval / 1800000ms timeout
- Entrypoint: privy.depositAddress.waitForDeposit({...})
- Attacker controls: depositAddressId, orderId, quoteCreatedAt, pollIntervalMs, timeoutMs, abort signal
- Exploit idea: Pass mixed-case and zero-padded variants of the USDC address.
- Invariant to test: Token identity comparison must be canonical.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: table-test address forms through getDeposit.
