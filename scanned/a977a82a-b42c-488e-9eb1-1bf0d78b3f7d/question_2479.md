# Q2479: off-chain length header is two bytes in client.ts

## Question
buildSolanaOffchainMessage writes the message length as two little-endian bytes and caps the total at 1232; can an attacker craft a length that disagrees with the payload so SolanaClient.invokeRpc or its parser reads a different message body?

## Target
- File/function: [src/solana/client.ts](src/solana/client.ts) - SolanaClient.invokeRpc, getBalance, getTokenAccountsByOwner, getAccountInfo (cluster.rpcUrl, errors swallowed to null)
- Entrypoint: new SolanaClient(cluster) balance/token reads used by funding UIs
- Attacker controls: cluster.rpcUrl value, RPC response shape, null-on-error results consumed as truth
- Exploit idea: Build and then parse a message whose declared length differs from the payload.
- Invariant to test: Declared length and payload must be verified equal on both build and parse.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: fuzz length/payload pairs through build and parse in SolanaClient.invokeRpc.
