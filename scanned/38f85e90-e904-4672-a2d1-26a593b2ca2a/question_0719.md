# Q0719: quantity parser rejects only some shapes in client.ts

## Question
toQuantity accepts numbers, bigints and 0x-hex but throws otherwise; can an attacker pass a value that survives the check yet decodes differently server-side through SolanaClient.invokeRpc?

## Target
- File/function: [src/solana/client.ts](src/solana/client.ts) - SolanaClient.invokeRpc, getBalance, getTokenAccountsByOwner, getAccountInfo (cluster.rpcUrl, errors swallowed to null)
- Entrypoint: new SolanaClient(cluster) balance/token reads used by funding UIs
- Attacker controls: cluster.rpcUrl value, RPC response shape, null-on-error results consumed as truth
- Exploit idea: Feed '0x0000...01', leading-zero hex and oversized values.
- Invariant to test: Quantity encoding must be canonical for every signed field.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: feed a canonicalisation table to SolanaClient.invokeRpc and assert a single normalised output.
