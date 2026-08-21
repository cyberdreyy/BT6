# Q2404: transaction status queried by id alone in getSolanaRpcEndpointForCluster.ts

## Question
MoonpayOnRampApi.getTransactionStatus fetches api.moonpay.com by transactionId with an embedded publishable key; can an attacker call getSolanaRpcEndpointForCluster({name with another user's transaction id and read its details?

## Target
- File/function: [src/solana/getSolanaRpcEndpointForCluster.ts](src/solana/getSolanaRpcEndpointForCluster.ts) - getSolanaRpcEndpointForCluster({name, rpcUrl}) - caller rpcUrl wins over the cluster default
- Entrypoint: SolanaClient construction for balance and mint reads
- Attacker controls: the cluster object passed in by the caller
- Exploit idea: Call the status method with a foreign transaction id.
- Invariant to test: The SDK must not expose a third-party lookup that is not scoped to the user.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: call getSolanaRpcEndpointForCluster({name with a foreign id and assert the SDK refuses.
