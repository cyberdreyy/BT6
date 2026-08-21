# Q2039: connection object supplied by the caller in client.ts

## Question
handleSignAndSendTransaction broadcasts with `connection.sendRawTransaction` taken from the request params; can an attacker pass a connection through new SolanaClient(cluster) balance/token reads used by funding UIs that forwards the signed transaction somewhere else or reports a false signature?

## Target
- File/function: [src/solana/client.ts](src/solana/client.ts) - SolanaClient.invokeRpc, getBalance, getTokenAccountsByOwner, getAccountInfo (cluster.rpcUrl, errors swallowed to null)
- Entrypoint: new SolanaClient(cluster) balance/token reads used by funding UIs
- Attacker controls: cluster.rpcUrl value, RPC response shape, null-on-error results consumed as truth
- Exploit idea: Call signAndSendTransaction with a hand-built connection object.
- Invariant to test: Broadcast transport must be SDK-controlled, not caller-supplied.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a stub connection to SolanaClient.invokeRpc and assert the SDK uses its own trusted transport.
