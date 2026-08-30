No vulnerability found for this question.

Analysis: The claim conflates two independent budgets that the protocol never intends to be the same value. `verify_function_call_permission` (verifier.rs:166-208) and `check_and_compute_new_allowance` (verifier.rs:239-260) enforce a one-time debit against the **signer's** `FunctionCallPermission.allowance` for the cost of admitting that single `FunctionCall` action as a transaction — `total_cost` there is `prepaid_fee + attached gas cost` of the outer transaction only, and this is by design decremented exactly once, in lockstep with the signer's account balance, before any receipt executes. [1](#0-0) 

Once that `FunctionCall` executes as a receipt on receiver R, any further promises R's contract spawns via `promise_batch_create`/`promise_batch_action_function_call_weight` are funded from **R's own account balance** (`result_state.current_account_balance` / `deduct_balance`) and **R's own remaining attached gas** (tracked by the gas counter that was granted to R when the receipt was created), not from the signer's allowance at all. [2](#0-1) [3](#0-2) 

This is intentional protocol semantics, not a missed re-validation: the FunctionCall access key's allowance is a spending cap the signer places on their own account for authorizing calls to a fixed `receiver_id`/`method_names`, deducted once when the transaction is admitted (mirrors the base account-balance debit at verifier.rs:310-320). It was never designed to also cap what the *receiver* does with its *own* balance/gas once it receives control — doing so would require threading the signer's remaining allowance into every downstream receipt across shards, which the protocol does not do and has never claimed to do. The gas actually consumed by nested promises is bounded by `attached_gas`/`prepaid_gas` mechanics documented in `docs/architecture/how/gas.md` (max 300 Tgas per transaction, distributed via gas weights), and any deposits attached to those nested calls come from R's balance, which R controls and is separately protected by R's own storage-staking and balance checks — not the signer's allowance invariant. [4](#0-3) 

Since there is no reachable path for an unprivileged attacker to make the signer's allowance under-decrement, over-decrement, or be bypassed for what it is actually meant to gate (transaction admission cost), and the "post-hoc promise deposits from R's balance" are R's own funds (not stolen from the signer), there is no value-conservation violation of the signer's allowance and no fund theft/freezing/inflation/authorization-escalation impact.

### Citations

**File:** runtime/runtime/src/verifier.rs (L239-260)
```rust
fn check_and_compute_new_allowance(
    access_key: &AccessKey,
    account_id: &AccountId,
    public_key: &PublicKey,
    total_cost: Balance,
) -> Result<Option<Balance>, InvalidTxError> {
    let Some(fc) = access_key.permission.function_call_permission() else {
        return Ok(None);
    };
    let Some(allowance) = fc.allowance else {
        return Ok(None);
    };
    let new_allowance = allowance.checked_sub(total_cost).ok_or_else(|| {
        InvalidTxError::InvalidAccessKeyError(InvalidAccessKeyError::NotEnoughAllowance {
            account_id: account_id.clone(),
            public_key: public_key.clone().into(),
            allowance,
            cost: total_cost,
        })
    })?;
    Ok(Some(new_allowance))
}
```

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3095-3121)
```rust
        // Prepaid gas
        self.result_state.gas_counter.prepay_gas(gas)?;
        // Allow attaching exactly 1 yoctoNEAR to a promise function call
        // when the contract has zero balance. This lets deterministic accounts
        // call functions like ft_transfer_call that require an attached deposit
        // without needing to be seeded with balance first.
        let skip_deduct = amount == Balance::from_yoctonear(1)
            && self.config.one_yocto_on_promise
            && self.result_state.current_account_balance.is_zero();
        if skip_deduct {
            self.result_state.subsidized_amount = self
                .result_state
                .subsidized_amount
                .checked_add(amount)
                .expect("subsidized_amount overflow");
        } else {
            self.result_state.deduct_balance(amount)?;
        }
        self.ext.append_action_function_call_weight(
            receipt_idx,
            method_name,
            arguments,
            amount,
            gas,
            GasWeight(gas_weight),
        )
    }
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L3293-3319)
```rust
    // Prepaid gas
    ctx.result_state.gas_counter.prepay_gas(Gas::from_gas(gas))?;
    // Allow attaching exactly 1 yoctoNEAR to a promise function call
    // when the contract has zero balance. This lets deterministic accounts
    // call functions like ft_transfer_call that require an attached deposit
    // without needing to be seeded with balance first.
    let skip_deduct = amount == Balance::from_yoctonear(1)
        && ctx.config.one_yocto_on_promise
        && ctx.result_state.current_account_balance.is_zero();
    if skip_deduct {
        ctx.result_state.subsidized_amount = ctx
            .result_state
            .subsidized_amount
            .checked_add(amount)
            .expect("subsidized_amount overflow");
    } else {
        ctx.result_state.deduct_balance(amount)?;
    }
    ctx.ext.append_action_function_call_weight(
        receipt_idx,
        method_name,
        arguments,
        amount,
        Gas::from_gas(gas),
        GasWeight(gas_weight),
    )
}
```

**File:** docs/architecture/how/gas.md (L123-142)
```markdown
The gas attached to a function can be at most `max_total_prepaid_gas`, which is
300 Tgas since the mainnet launch. Note that this limit is per
`SignedTransaction`, not per function call. In other words, batched function
calls share this limit.

There is also a limit to how much single call can burn, `max_gas_burnt`, which
used to be 200 Tgas but has been increased to 300 Tgas in protocol version 52.
(Note: When attaching gas to an outgoing function call, this is not counted as
gas burnt.) However, given a call can never burn more than was attached anyway,
this second limit is obsolete with the current configuration where the two limits
are equal.

Since protocol version 53, with the stabilization of
[NEP-264](https://github.com/near/NEPs/blob/master/neps/nep-0264.md), contract
developers do not have to specify the absolute amount of gas to attach to calls.
`promise_batch_action_function_call_weight` allows to specify a ratio of unspent
gas that is computed after the current call has finished. This allows attaching
100% of unspent gas to a call. If there are multiple calls, this allows
attaching an equal fraction to each, or any other split as defined by the weight
per call.
```
