# Q1186: poll swallows every operation error in polling.ts

## Question
poll catches all errors, records the last one and keeps iterating; can an attacker cause repeated authorization failures inside getDeposit to be hidden until max_attempts, so the app keeps polling with a stale session?

## Target
- File/function: [src/action/depositAddress/polling.ts](src/action/depositAddress/polling.ts) - getDeposit, waitForDeposit (GetNextDepositAddressOrder with after=quoteCreatedAt), waitForCompletion (until status !== 'executing'), 2000ms interval / 1800000ms timeout
- Entrypoint: privy.depositAddress.waitForDeposit({...})
- Attacker controls: depositAddressId, orderId, quoteCreatedAt, pollIntervalMs, timeoutMs, abort signal
- Exploit idea: Return 401s from the polled route and observe the loop behaviour.
- Invariant to test: Authorization failures must terminate polling immediately.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return 401 from getDeposit's operation and assert immediate termination.
