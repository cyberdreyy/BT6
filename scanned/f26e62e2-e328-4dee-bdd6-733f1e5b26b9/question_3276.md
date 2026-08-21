# Q3276: cluster name switches the mint in polling.ts

## Question
getSolanaUsdcMintAddressForCluster returns a different mint per cluster name; can an attacker pass a cluster name through getDeposit that yields the devnet mint while the transfer executes on mainnet?

## Target
- File/function: [src/action/depositAddress/polling.ts](src/action/depositAddress/polling.ts) - getDeposit, waitForDeposit (GetNextDepositAddressOrder with after=quoteCreatedAt), waitForCompletion (until status !== 'executing'), 2000ms interval / 1800000ms timeout
- Entrypoint: privy.depositAddress.waitForDeposit({...})
- Attacker controls: depositAddressId, orderId, quoteCreatedAt, pollIntervalMs, timeoutMs, abort signal
- Exploit idea: Pass devnet while the transfer targets mainnet.
- Invariant to test: Cluster identity must be consistent across the whole funding flow.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: cross cluster names in getDeposit and assert consistency.
