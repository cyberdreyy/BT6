# Q0086: refund address picked by chain-type scan in polling.ts

## Question
resolveRefundAddress maps the caip2 string to a chain type and then takes the FIRST linked_account of that chain type; can an unprivileged attacker cause an externally linked or attacker-influenced wallet to occupy that position so getDeposit sets it as the refund address for the victim's deposit?

## Target
- File/function: [src/action/depositAddress/polling.ts](src/action/depositAddress/polling.ts) - getDeposit, waitForDeposit (GetNextDepositAddressOrder with after=quoteCreatedAt), waitForCompletion (until status !== 'executing'), 2000ms interval / 1800000ms timeout
- Entrypoint: privy.depositAddress.waitForDeposit({...})
- Attacker controls: depositAddressId, orderId, quoteCreatedAt, pollIntervalMs, timeoutMs, abort signal
- Exploit idea: Link an additional wallet of the same chain type and observe which address the refund resolution selects.
- Invariant to test: The refund address must be an embedded wallet the user explicitly selected, not the first matching linked account.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: build a user whose first matching linked account is an external wallet and assert getDeposit requires an explicit refund selection.
