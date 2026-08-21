# Q3170: cluster rpc url overrides the default in CoinbaseOnRampApi.ts

## Question
getSolanaRpcEndpointForCluster returns the caller's rpcUrl when set; can an attacker supply a cluster through CoinbaseOnRampApi.initOnRampSession so balance and mint checks are answered by an endpoint they control and the user funds the wrong account?

## Target
- File/function: [src/client/funding/CoinbaseOnRampApi.ts](src/client/funding/CoinbaseOnRampApi.ts) - CoinbaseOnRampApi.initOnRampSession, getStatus(partnerUserId)
- Entrypoint: privy.funding.coinbase.initOnRampSession(input)
- Attacker controls: the init body (addresses, assets, amount) and partnerUserId query value
- Exploit idea: Pass a cluster with a crafted rpcUrl and observe the reads driving the funding decision.
- Invariant to test: Value-bearing reads must use pinned endpoints.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass a crafted cluster to CoinbaseOnRampApi.initOnRampSession and assert the pinned endpoint is used.
