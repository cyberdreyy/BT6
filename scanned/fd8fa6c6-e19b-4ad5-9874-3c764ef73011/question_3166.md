# Q3166: cluster rpc url overrides the default in polling.ts

## Question
getSolanaRpcEndpointForCluster returns the caller's rpcUrl when set; can an attacker supply a cluster through getDeposit so balance and mint checks are answered by an endpoint they control and the user funds the wrong account?

## Target
- File/function: [src/action/depositAddress/polling.ts](src/action/depositAddress/polling.ts) - getDeposit, waitForDeposit (GetNextDepositAddressOrder with after=quoteCreatedAt), waitForCompletion (until status !== 'executing'), 2000ms interval / 1800000ms timeout
- Entrypoint: privy.depositAddress.waitForDeposit({...})
- Attacker controls: depositAddressId, orderId, quoteCreatedAt, pollIntervalMs, timeoutMs, abort signal
- Exploit idea: Pass a cluster with a crafted rpcUrl and observe the reads driving the funding decision.
- Invariant to test: Value-bearing reads must use pinned endpoints.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass a crafted cluster to getDeposit and assert the pinned endpoint is used.
