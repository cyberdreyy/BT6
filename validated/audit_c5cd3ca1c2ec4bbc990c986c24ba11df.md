### Title
Deposits sent via `TransferToGasKeyAction` are permanently destroyed with no refund when the target gas key no longer exists (e.g., after `DeleteAccount`) - ([File: runtime/runtime/src/access_keys.rs])

### Summary
`action_transfer_to_gas_key` silently drops the attached deposit when the target access key/gas key is missing, only setting an action error and returning `Ok(())` without emitting any refund receipt. Because cross-shard receipts (including `TransferToGasKeyAction`) are asynchronous, an account owner can delete their account (or the specific gas key) after a third party has already sent a `TransferToGasKeyAction` receipt but before that receipt lands on the target shard, causing the sender's deposit to be permanently burned with no compensation.

### Finding Description
`action_transfer_to_gas_key` (runtime/runtime/src/access_keys.rs:257-288) looks up the access key via `get_access_key`. If it is absent, or present but not a gas key, it sets `result.result = Err(ActionErrorKind::GasKeyDoesNotExist{...})` and returns `Ok(())`: [1](#0-0) 

There is no call that creates a `Receipt::new_balance_refund` (or any other compensating receipt) for `action.deposit` in this failure branch — the deposit that A attached when constructing the `TransferToGasKeyAction` simply vanishes from the ledger once this action returns.

The gas key (and its associated balance) can cease to exist for a legitimate, unprivileged reason: the account owner V calls `DeleteAccount`, which computes and burns the sum of all gas key balances present at that time (`compute_gas_key_balance_sum` in `runtime/runtime/src/actions.rs`) and then removes the account's access keys/gas keys from the trie. Because NEAR receipts are processed asynchronously across shards (a receipt sent on shard X is forwarded and only applied on shard Y in a later block), it is possible for:
1. Attacker A to send `TransferToGasKeyAction` targeting V's gas key.
2. Before that receipt is delivered and applied on V's shard, V submits and applies `DeleteAccount` on their own account (removing the gas key and its balance snapshot).
3. When A's `TransferToGasKeyAction` receipt later executes against V's (now-deleted) account, `get_access_key` returns `None`, hitting the `GasKeyDoesNotExist` branch, and A's deposit is lost with no refund.

None of the existing signature, nonce, access-key permission, or balance checks intervene here — those checks are all satisfied at the time A signs and submits the transaction; the failure occurs purely because of receipt-arrival timing versus V's independent, legitimate `DeleteAccount` action.

### Impact Explanation
This is a token-loss ("silent burn") bug: value that a third party (A) attached to a receipt is destroyed without being credited to any account and without generating a compensating refund receipt, violating value-conservation invariants for a victim who is not the party performing the account deletion. This matches the "token inflation or loss" bounty category (specifically deflation/permanent loss of user funds) via total-supply/on-chain balance divergence.

### Likelihood Explanation
The precondition — an ordinary account owner deleting their own account while an in-flight cross-shard deposit is still outstanding — is a routine, non-adversarial (from V's perspective) occurrence and requires no privileged access. From A's perspective, they simply need to (unknowingly or through targeted timing by a malicious V) send a `TransferToGasKeyAction` to an account whose owner deletes the account before the receipt is delivered, which is trivially reproducible in a two-shard cross-shard test scenario. The same silent-loss code path (`GasKeyDoesNotExist` with no refund) is also reachable more generally any time a gas key is deleted via `DeleteKeyAction` between the sending and application of a `TransferToGasKeyAction`, not only via `DeleteAccount`, making it a broader and repeatable class of loss.

### Recommendation
When `action_transfer_to_gas_key` (and similarly `action_withdraw_from_gas_key`) fails because the target key/gas key does not exist, generate a `Receipt::new_balance_refund` back to the receipt's predecessor for `action.deposit`, mirroring the refund behavior used elsewhere (e.g., in `action_delete_account`) so that deposits are never silently destroyed on a missing-key failure.

### Proof of Concept
Integration/test-loop test across two shards:
1. Create account V on shard S1 with a gas key K (via `AddKeyAction` with `GasKeyInfo`).
2. From account A on shard S2, submit a transaction containing `TransferToGasKeyAction{public_key: K, deposit: D}` targeting V, forcing the resulting receipt to be forwarded cross-shard to S1.
3. In the same or an earlier block on S1 (before the forwarded receipt is delivered/applied), have V submit `DeleteAccountAction{beneficiary_id: V}` (or delete key K via `DeleteKeyAction`), deleting the gas key.
4. Advance chunks so the forwarded `TransferToGasKeyAction` receipt is applied against the now-nonexistent account/key.
5. Assert: total token supply before == total token supply after (accounting for gas burnt), and that D is either credited to some account or refunded to A — never unaccounted for. Expected (buggy) result: D disappears from all account balances and is not recorded as `tokens_burnt`, demonstrating silent value loss.

### Citations

**File:** runtime/runtime/src/access_keys.rs (L262-279)
```rust
) -> Result<(), RuntimeError> {
    let Some(mut access_key) = get_access_key(state_update, account_id, &action.public_key)? else {
        result.result = Err(ActionErrorKind::GasKeyDoesNotExist {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
        }
        .into());
        return Ok(());
    };
    let Some(gas_key_info) = access_key.gas_key_info_mut() else {
        // Key exists but is not a gas key
        result.result = Err(ActionErrorKind::GasKeyDoesNotExist {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
        }
        .into());
        return Ok(());
    };
```
