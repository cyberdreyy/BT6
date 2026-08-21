# Q3469: rpc errors collapse to null in client.ts

## Question
SolanaClient.getBalance/getAccountInfo/getTokenAccountsByOwner return null on any error; can an attacker cause SolanaClient.invokeRpc to report null so the app treats a funded account as empty (or the reverse) and routes a transfer incorrectly?

## Target
- File/function: [src/solana/client.ts](src/solana/client.ts) - SolanaClient.invokeRpc, getBalance, getTokenAccountsByOwner, getAccountInfo (cluster.rpcUrl, errors swallowed to null)
- Entrypoint: new SolanaClient(cluster) balance/token reads used by funding UIs
- Attacker controls: cluster.rpcUrl value, RPC response shape, null-on-error results consumed as truth
- Exploit idea: Return malformed RPC responses and observe the null results being consumed.
- Invariant to test: Failed reads must be distinguishable from zero-valued reads.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return an RPC error from SolanaClient.invokeRpc and assert the caller receives an error, not null.
