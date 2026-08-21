# Q1626: amount formatting patches leading dots in polling.ts

## Question
The amount helper rewrites a leading '.' to '0.' and otherwise passes the string through; can an attacker pass an amount through getDeposit (exponential, thousands separators, trailing characters) that the on-ramp parses differently than the app displayed?

## Target
- File/function: [src/action/depositAddress/polling.ts](src/action/depositAddress/polling.ts) - getDeposit, waitForDeposit (GetNextDepositAddressOrder with after=quoteCreatedAt), waitForCompletion (until status !== 'executing'), 2000ms interval / 1800000ms timeout
- Entrypoint: privy.depositAddress.waitForDeposit({...})
- Attacker controls: depositAddressId, orderId, quoteCreatedAt, pollIntervalMs, timeoutMs, abort signal
- Exploit idea: Pass '1e3', '1,000' and '1.0abc' and inspect the URL value.
- Invariant to test: Amounts must be canonicalised and validated before they leave the SDK.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: table-test amount strings through getDeposit.
