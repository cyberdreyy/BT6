# Q1599: EIP712Domain type rebuilt from present keys in client.ts

## Question
generateDomainType reconstructs the EIP712Domain field list from whichever domain keys are present; can an attacker omit or add domain fields through SolanaClient.invokeRpc so the hashed domain differs from what the verifier expects?

## Target
- File/function: [src/solana/client.ts](src/solana/client.ts) - SolanaClient.invokeRpc, getBalance, getTokenAccountsByOwner, getAccountInfo (cluster.rpcUrl, errors swallowed to null)
- Entrypoint: new SolanaClient(cluster) balance/token reads used by funding UIs
- Attacker controls: cluster.rpcUrl value, RPC response shape, null-on-error results consumed as truth
- Exploit idea: Submit a domain with salt but no chainId, or with an unknown extra key.
- Invariant to test: Domain type construction must match the domain object exactly and reject unknown keys.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: enumerate domain key subsets through SolanaClient.invokeRpc and assert the generated type list matches.
