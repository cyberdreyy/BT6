No vulnerability found for this question.

The `saturating_add` in `try_refund_allowance` is intentional and documented behavior — it's explicitly described in `docs/RuntimeSpec/Refunds.md:63` ("the runtime uses saturating add to increase the allowance, to avoid overflows"), and the only downstream reader of the stored allowance, `check_and_compute_new_allowance` in `verifier.rs:239-260`, performs `allowance.checked_sub(total_cost)` directly on whatever value is currently stored — it does not independently re-derive or recompute the refunded value and compare it against the saturated one. There is no dual-computation/mismatch pattern here. [1](#0-0) [2](#0-1) [3](#0-2) 

Additionally, since `Balance` (a `u128`) values on this path are bounded in practice by the total NEAR token supply (nowhere near `u128::MAX`), the `saturating_add` in `try_refund_allowance` would never actually clamp/saturate under any realistic funded-account scenario reachable by an unprivileged attacker — matching the same reasoning already documented elsewhere in the codebase about total supply never approaching `u128::MAX`. There is no reachable transaction sequence (regular refund path or gas-key path) where this saturation diverges from a "checked" expectation elsewhere, and no panic results from `checked_sub` returning `None` (it is handled via `InvalidTxError::NotEnoughAllowance`, not `.unwrap()`). [4](#0-3)

### Citations

**File:** runtime/runtime/src/actions.rs (L134-157)
```rust
pub(crate) fn try_refund_allowance(
    state_update: &mut TrieUpdate,
    account_id: &AccountId,
    public_key: &PublicKey,
    deposit: Balance,
) -> Result<(), StorageError> {
    if let Some(mut access_key) = get_access_key(state_update, account_id, public_key)? {
        let mut updated = false;
        if let AccessKeyPermission::FunctionCall(function_call_permission) =
            &mut access_key.permission
        {
            if let Some(allowance) = function_call_permission.allowance.as_mut() {
                let new_allowance = allowance.saturating_add(deposit);
                if new_allowance > *allowance {
                    *allowance = new_allowance;
                    updated = true;
                }
            }
        }
        if updated {
            set_access_key(state_update, account_id.clone(), public_key.clone(), &access_key);
        }
    }
    Ok(())
```

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

**File:** docs/RuntimeSpec/Refunds.md (L49-63)
```markdown
## Access Key Allowance refunds

When an account used a restricted access key with `FunctionCallPermission`, it may have had a limited allowance.
The allowance was charged for the full amount of receipt fees including full prepaid gas.
To refund the allowance we distinguish between Deposit refunds and Gas refunds using `signer_id` in the action receipt.

If the `signer_id == receiver_id && predecessor_id == "system"` it means it's a gas refund and the runtime should try to refund the allowance.

Note, that it's not always possible to refund the allowance, because the access key can be deleted between the moment when the transaction was
issued and when the gas refund arrived. In this case we use the best effort to refund the allowance. It means:

- the access key on the `signer_id` account with the public key `signer_public_key` should exist
- the access key permission should be `FunctionCallPermission`
- the allowance should be set to `Some` limited value, instead of unlimited allowance (`None`)
- the runtime uses saturating add to increase the allowance, to avoid overflows
```
