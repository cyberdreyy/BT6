# Q1049: access list normalisation drops entries in client.ts

## Question
toAccessList handles arrays, tuple pairs and objects; can an attacker craft an access list through SolanaClient.invokeRpc that is silently reshaped so the signed transaction differs from the approved one?

## Target
- File/function: [src/solana/client.ts](src/solana/client.ts) - SolanaClient.invokeRpc, getBalance, getTokenAccountsByOwner, getAccountInfo (cluster.rpcUrl, errors swallowed to null)
- Entrypoint: new SolanaClient(cluster) balance/token reads used by funding UIs
- Attacker controls: cluster.rpcUrl value, RPC response shape, null-on-error results consumed as truth
- Exploit idea: Send an access list in each accepted shape and compare the serialised result.
- Invariant to test: Access-list normalisation must be lossless.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: round-trip every access-list shape through SolanaClient.invokeRpc.
