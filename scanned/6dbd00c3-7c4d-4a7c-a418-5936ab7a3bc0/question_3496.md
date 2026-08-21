# Q3496: deposit config fetched but not enforced in polling.ts

## Question
getConfig returns currencies and chains but the generate path does not consult it; can an attacker submit a quote through getDeposit for a pair the config excludes?

## Target
- File/function: [src/action/depositAddress/polling.ts](src/action/depositAddress/polling.ts) - getDeposit, waitForDeposit (GetNextDepositAddressOrder with after=quoteCreatedAt), waitForCompletion (until status !== 'executing'), 2000ms interval / 1800000ms timeout
- Entrypoint: privy.depositAddress.waitForDeposit({...})
- Attacker controls: depositAddressId, orderId, quoteCreatedAt, pollIntervalMs, timeoutMs, abort signal
- Exploit idea: Submit an excluded pair after fetching the config.
- Invariant to test: The client must enforce the fetched configuration before creating a quote.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit an excluded pair to getDeposit and assert refusal.
