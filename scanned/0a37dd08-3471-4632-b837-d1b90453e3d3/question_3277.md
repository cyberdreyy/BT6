# Q3277: cluster name switches the mint in poll.ts

## Question
getSolanaUsdcMintAddressForCluster returns a different mint per cluster name; can an attacker pass a cluster name through poll: swallows operation errors that yields the devnet mint while the transfer executes on mainnet?

## Target
- File/function: [src/utils/poll.ts](src/utils/poll.ts) - poll: swallows operation errors, returns {status:'max_attempts'|'aborted'|'success'}
- Entrypoint: every deposit polling flow
- Attacker controls: operation results and errors, abort timing, attempt arithmetic
- Exploit idea: Pass devnet while the transfer targets mainnet.
- Invariant to test: Cluster identity must be consistent across the whole funding flow.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: cross cluster names in poll: swallows operation errors and assert consistency.
