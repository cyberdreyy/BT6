### Title
`get_expected_withdrawals` does not cap cumulative pending partial withdrawals to a validator's actual withdrawable balance - (File: `specs/electra/beacon-chain.md`)

### Summary
The Electra `get_expected_withdrawals` function iterates over `state.pending_partial_withdrawals` and, for each pending withdrawal belonging to a validator, computes the withdrawable amount as `min(state.balances[validator_index] - MIN_ACTIVATION_BALANCE, withdrawal.amount)`. This check is performed independently for each entry against the validator's *current* `state.balances[validator_index]`, without subtracting withdrawals already selected earlier in the same sweep for the same validator index. Consequently multiple pending withdrawals for one validator can each individually pass the `has_excess_balance` check while their sum exceeds the validator's real spare balance.

### Finding Description
Within the loop over `pending_partial_withdrawals` in `get_expected_withdrawals` (`specs/electra/beacon-chain.md`), each withdrawal is evaluated using:

```
has_excess_balance = state.balances[withdrawal.validator_index] > MIN_ACTIVATION_BALANCE
...
withdrawable_balance = min(
    state.balances[withdrawal.validator_index] - MIN_ACTIVATION_BALANCE,
    withdrawal.amount,
)
```

`state.balances[...]` is read fresh from state for every iteration and is never decremented to reflect withdrawals already appended to the `withdrawals` list within the same call. If two (or more) pending withdrawal entries exist for the same validator index and each individually satisfies `amount <= balance - MIN_ACTIVATION_BALANCE`, both are appended in full, even though their sum can exceed `balance - MIN_ACTIVATION_BALANCE`. The invariant "total amount withdrawn per validator this sweep ≤ balance - MIN_ACTIVATION_BALANCE" is never enforced anywhere in this function.

Downstream, `process_withdrawals` in the same file computes, for the sweep/full-withdrawal path:
```
partially_withdrawn_balance = sum(withdrawal.amount for withdrawal in withdrawals if withdrawal.validator_index == validator_index)
balance = state.balances[validator_index] - partially_withdrawn_balance
```
Because Gwei-typed subtraction is unsigned, if `partially_withdrawn_balance` exceeds `state.balances[validator_index]` (which the flawed selection in `get_expected_withdrawals` permits), this line underflows. The spec text itself does not define saturating/clamped behavior for this subtraction, so it is genuinely ambiguous at the specification level, not merely a client implementation detail.

### Impact Explanation
Because the invariant is missing directly in `get_expected_withdrawals` (the spec function, not a client), the resulting underflow is a specification-level ambiguity: different client implementations resolve the unsigned subtraction differently (wrap-around, clamp-to-zero, error). This means spec-following clients, when correctly implementing the literal pseudocode, can diverge on the resulting balance/withdrawal amount for the same block and state — a consensus (finality/safety) split caused purely by following the spec as written, with no malicious peer or foreign client bug required. This satisfies the "Critical: invalid transition accepted/rejected causing spec-following nodes to split" criterion, and separately allows more Gwei to be withdrawn from a validator than it actually possesses (Gwei paid to non-owner beyond entitlement).

### Likelihood Explanation
Triggering the scenario requires: (1) a validator with compounding withdrawal credentials queuing two or more partial withdrawals whose sum is close to `balance - MIN_ACTIVATION_BALANCE`, (2) a sufficiently backlogged `pending_partial_withdrawals` queue so multiple entries are processed together, and (3) balance shrinkage between request and execution (e.g., inactivity leak) so the sum of already-queued amounts now exceeds available spare balance. These are non-adversarial, naturally occurring conditions (leak during non-finality) rather than requiring any protocol violation, making it a plausible, if narrow, edge case rather than a purely theoretical one.

### Recommendation
In `get_expected_withdrawals`, track a running per-validator "already withdrawn this sweep" accumulator (or decrement a local copy of `state.balances[validator_index]`) as each pending partial withdrawal is processed, and clamp each subsequent withdrawal's amount to `remaining_balance - MIN_ACTIVATION_BALANCE`, ensuring the cumulative amount selected for any single validator index can never exceed its current balance minus `MIN_ACTIVATION_BALANCE`. This removes the possibility of `partially_withdrawn_balance` exceeding `state.balances[validator_index]` downstream in `process_withdrawals`, eliminating the underflow ambiguity entirely at the spec level.

### Proof of Concept
1. Validator A holds compounding credentials, balance = effective balance = 2048 ETH.
2. A submits withdrawal request 1 for 1008 ETH → passes `2048 - 1008 = 1040 ≥ MIN_ACTIVATION_BALANCE (32)`; queued.
3. A submits withdrawal request 2 for 1008 ETH → passes same check against unchanged `state.balances[A]` (still 2048, since queuing doesn't debit balance) → queued.
4. Chain fails to finalize; A goes offline and leaks down to 2015 ETH while both withdrawals remain queued.
5. At sweep time, `get_expected_withdrawals` evaluates entry 1: `has_excess_balance = 2015 > 32` true; `withdrawable_balance = min(2015-32, 1008) = 1008`. Entry 2 independently: `has_excess_balance` again computed against the *same unmodified* `state.balances[A] = 2015`; `withdrawable_balance = min(2015-32, 1008) = 1008`. Both appended → total withdrawals for A = 2016 > 2015 - 32 = 1983 available.
6. In `process_withdrawals`, `partially_withdrawn_balance = 2016`, `balance = 2015 - 2016` underflows (unsigned Gwei), producing divergent results across implementations that follow the literal spec pseudocode differently. [1](#0-0)

### Citations

**File:** specs/electra/beacon-chain.md (L1-1)
```markdown
# Electra -- The Beacon Chain
```
