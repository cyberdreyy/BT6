# Q2294: moonpay sign input forwarded verbatim in getSolanaRpcEndpointForCluster.ts

## Question
MoonpayOnRampApi.sign posts the caller's input body to the signing route; can an attacker include a walletAddress in getSolanaRpcEndpointForCluster({name that is not theirs so the signed on-ramp URL delivers funds elsewhere?

## Target
- File/function: [src/solana/getSolanaRpcEndpointForCluster.ts](src/solana/getSolanaRpcEndpointForCluster.ts) - getSolanaRpcEndpointForCluster({name, rpcUrl}) - caller rpcUrl wins over the cluster default
- Entrypoint: SolanaClient construction for balance and mint reads
- Attacker controls: the cluster object passed in by the caller
- Exploit idea: Submit a foreign wallet address in the sign input.
- Invariant to test: The funded address must be validated against the authenticated user's wallets.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit a foreign address to getSolanaRpcEndpointForCluster({name and assert rejection.
