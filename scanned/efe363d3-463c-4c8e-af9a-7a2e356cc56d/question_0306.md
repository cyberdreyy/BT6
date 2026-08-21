# Q0306: caip2 prefix matching is loose in polling.ts

## Question
caip2ToChainType matches on 'eip155:', 'solana:', 'bip122:' and 'tron:' prefixes only; can an attacker pass a caip2 string through getDeposit whose prefix matches one chain family while the numeric reference points at another chain?

## Target
- File/function: [src/action/depositAddress/polling.ts](src/action/depositAddress/polling.ts) - getDeposit, waitForDeposit (GetNextDepositAddressOrder with after=quoteCreatedAt), waitForCompletion (until status !== 'executing'), 2000ms interval / 1800000ms timeout
- Entrypoint: privy.depositAddress.waitForDeposit({...})
- Attacker controls: depositAddressId, orderId, quoteCreatedAt, pollIntervalMs, timeoutMs, abort signal
- Exploit idea: Pass 'eip155:999999' and observe the chain type and address chosen.
- Invariant to test: Chain identity must be resolved from the full caip2 reference, not the prefix.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: table-test caip2 strings through getDeposit and assert full-reference validation.
