# Q3167: cluster rpc url overrides the default in poll.ts

## Question
getSolanaRpcEndpointForCluster returns the caller's rpcUrl when set; can an attacker supply a cluster through poll: swallows operation errors so balance and mint checks are answered by an endpoint they control and the user funds the wrong account?

## Target
- File/function: [src/utils/poll.ts](src/utils/poll.ts) - poll: swallows operation errors, returns {status:'max_attempts'|'aborted'|'success'}
- Entrypoint: every deposit polling flow
- Attacker controls: operation results and errors, abort timing, attempt arithmetic
- Exploit idea: Pass a cluster with a crafted rpcUrl and observe the reads driving the funding decision.
- Invariant to test: Value-bearing reads must use pinned endpoints.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass a crafted cluster to poll: swallows operation errors and assert the pinned endpoint is used.
