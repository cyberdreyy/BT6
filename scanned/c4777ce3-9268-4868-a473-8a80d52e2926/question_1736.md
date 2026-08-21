# Q1736: asset id map lookup unchecked in polling.ts

## Question
toCoinbaseAssetId falls back to 'ETH' for anything that is not USDC on known chains, and the asset id map is keyed by symbol; can an attacker choose a chain/asset pair through getDeposit so the on-ramp buys a different asset than the user selected?

## Target
- File/function: [src/action/depositAddress/polling.ts](src/action/depositAddress/polling.ts) - getDeposit, waitForDeposit (GetNextDepositAddressOrder with after=quoteCreatedAt), waitForCompletion (until status !== 'executing'), 2000ms interval / 1800000ms timeout
- Entrypoint: privy.depositAddress.waitForDeposit({...})
- Attacker controls: depositAddressId, orderId, quoteCreatedAt, pollIntervalMs, timeoutMs, abort signal
- Exploit idea: Pass an unsupported chain with asset USDC and inspect the resulting defaultAsset.
- Invariant to test: Unsupported asset/chain pairs must be rejected, never defaulted.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass unsupported pairs to getDeposit and assert rejection.
