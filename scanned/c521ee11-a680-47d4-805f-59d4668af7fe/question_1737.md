# Q1737: asset id map lookup unchecked in poll.ts

## Question
toCoinbaseAssetId falls back to 'ETH' for anything that is not USDC on known chains, and the asset id map is keyed by symbol; can an attacker choose a chain/asset pair through poll: swallows operation errors so the on-ramp buys a different asset than the user selected?

## Target
- File/function: [src/utils/poll.ts](src/utils/poll.ts) - poll: swallows operation errors, returns {status:'max_attempts'|'aborted'|'success'}
- Entrypoint: every deposit polling flow
- Attacker controls: operation results and errors, abort timing, attempt arithmetic
- Exploit idea: Pass an unsupported chain with asset USDC and inspect the resulting defaultAsset.
- Invariant to test: Unsupported asset/chain pairs must be rejected, never defaulted.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass unsupported pairs to poll: swallows operation errors and assert rejection.
