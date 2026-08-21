# Q3579: token account picked with .at(0) in client.ts

## Question
getTokenAccountsByOwner takes the first returned account's parsed amount; can an attacker cause multiple token accounts to be returned so SolanaClient.invokeRpc reports a balance from an account the user does not control?

## Target
- File/function: [src/solana/client.ts](src/solana/client.ts) - SolanaClient.invokeRpc, getBalance, getTokenAccountsByOwner, getAccountInfo (cluster.rpcUrl, errors swallowed to null)
- Entrypoint: new SolanaClient(cluster) balance/token reads used by funding UIs
- Attacker controls: cluster.rpcUrl value, RPC response shape, null-on-error results consumed as truth
- Exploit idea: Return several accounts including a zero-balance decoy first.
- Invariant to test: Balance aggregation must consider every matching account.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return multiple accounts from SolanaClient.invokeRpc's RPC stub and assert correct aggregation.
