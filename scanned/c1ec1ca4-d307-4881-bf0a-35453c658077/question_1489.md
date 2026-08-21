# Q1489: typed data domain unchecked against the chain in client.ts

## Question
The domain (chainId, verifyingContract) is forwarded verbatim; can an attacker sign typed data whose domain chainId differs from the provider chain via SolanaClient.invokeRpc, producing a signature valid on another chain?

## Target
- File/function: [src/solana/client.ts](src/solana/client.ts) - SolanaClient.invokeRpc, getBalance, getTokenAccountsByOwner, getAccountInfo (cluster.rpcUrl, errors swallowed to null)
- Entrypoint: new SolanaClient(cluster) balance/token reads used by funding UIs
- Attacker controls: cluster.rpcUrl value, RPC response shape, null-on-error results consumed as truth
- Exploit idea: Submit typed data with a foreign domain.chainId while the provider is on mainnet.
- Invariant to test: The typed-data domain must agree with the provider's active chain.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a mismatched domain chainId to SolanaClient.invokeRpc and assert rejection.
