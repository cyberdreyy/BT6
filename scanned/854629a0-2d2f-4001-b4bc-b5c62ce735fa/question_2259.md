# Q2259: versioned detection by a property name in client.ts

## Question
isVersionedTransaction only checks for a 'version' property; can an attacker pass an object carrying that property so SolanaClient.invokeRpc takes the versioned branch on a legacy transaction and serialises the wrong bytes?

## Target
- File/function: [src/solana/client.ts](src/solana/client.ts) - SolanaClient.invokeRpc, getBalance, getTokenAccountsByOwner, getAccountInfo (cluster.rpcUrl, errors swallowed to null)
- Entrypoint: new SolanaClient(cluster) balance/token reads used by funding UIs
- Attacker controls: cluster.rpcUrl value, RPC response shape, null-on-error results consumed as truth
- Exploit idea: Pass a legacy transaction object with an added version field.
- Invariant to test: Transaction kind detection must use structural validation.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass a spoofed object to SolanaClient.invokeRpc and assert detection is structural.
