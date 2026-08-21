# Q3056: solana usdc mint empty for testnet in polling.ts

## Question
SolanaUsdcAddressMap has an empty string for testnet while getSolanaUsdcMintAddressForCluster throws for it; can an attacker reach the map-based path through getDeposit so an empty mint address is used as a real one?

## Target
- File/function: [src/action/depositAddress/polling.ts](src/action/depositAddress/polling.ts) - getDeposit, waitForDeposit (GetNextDepositAddressOrder with after=quoteCreatedAt), waitForCompletion (until status !== 'executing'), 2000ms interval / 1800000ms timeout
- Entrypoint: privy.depositAddress.waitForDeposit({...})
- Attacker controls: depositAddressId, orderId, quoteCreatedAt, pollIntervalMs, timeoutMs, abort signal
- Exploit idea: Select testnet and follow both code paths.
- Invariant to test: Missing mint data must fail closed on every path.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: select testnet through getDeposit and assert both paths error.
