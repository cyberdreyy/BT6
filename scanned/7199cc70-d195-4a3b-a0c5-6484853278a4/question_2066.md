# Q2066: moonpay support check precedes the mapping in polling.ts

## Question
isSupportedChainIdForMoonpay warns and returns false for unknown assets while the mapping still runs elsewhere; can an attacker call getDeposit in an order that skips the support check?

## Target
- File/function: [src/action/depositAddress/polling.ts](src/action/depositAddress/polling.ts) - getDeposit, waitForDeposit (GetNextDepositAddressOrder with after=quoteCreatedAt), waitForCompletion (until status !== 'executing'), 2000ms interval / 1800000ms timeout
- Entrypoint: privy.depositAddress.waitForDeposit({...})
- Attacker controls: depositAddressId, orderId, quoteCreatedAt, pollIntervalMs, timeoutMs, abort signal
- Exploit idea: Call the mapping directly without the support check.
- Invariant to test: Currency mapping must be unreachable without a passing support check.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert getDeposit performs the support check internally.
