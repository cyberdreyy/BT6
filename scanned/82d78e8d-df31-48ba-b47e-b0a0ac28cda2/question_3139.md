# Q3139: array return shape collapses multi-sign results in client.ts

## Question
The wrapper returns t[0] for single-input calls and spreads otherwise; can an attacker submit multiple inputs through SolanaClient.invokeRpc so the caller associates the wrong signature with the wrong transaction?

## Target
- File/function: [src/solana/client.ts](src/solana/client.ts) - SolanaClient.invokeRpc, getBalance, getTokenAccountsByOwner, getAccountInfo (cluster.rpcUrl, errors swallowed to null)
- Entrypoint: new SolanaClient(cluster) balance/token reads used by funding UIs
- Attacker controls: cluster.rpcUrl value, RPC response shape, null-on-error results consumed as truth
- Exploit idea: Call signAndSendAllTransactions with several transactions and inspect the ordering guarantees.
- Invariant to test: Results must remain positionally bound to their inputs.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: assert SolanaClient.invokeRpc preserves input/output ordering for multi-input calls.
