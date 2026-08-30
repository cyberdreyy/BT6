### No vulnerability found for this question.

The core premise — that a gas key's balance can be "double-counted (once inside account.amount() and once inside compute_gas_key_balance_sum)" — does not hold. Gas key balances are stored in a completely separate field from account balance: `GasKeyInfo.balance` lives inside the `AccessKey`'s `permission` field in the trie [1](#0-0) , while `account.amount()` is a distinct field on the `Account` struct. These two values are never merged; movement between them only happens through explicit, balance-conserving actions: `action_transfer_to_gas_key` moves value from `account.amount()`-adjacent deposit into `gas_key_info.balance` via `checked_add` [2](#0-1) , and `action_withdraw_from_gas_key` moves it back with a `checked_sub`/`checked_add` pair [3](#0-2) .

In `action_delete_account`, `compute_gas_key_balance_sum` sums only the gas-key balances (skipping nonce entries) [4](#0-3) , and `account_balance` is `account_ref.amount()`, a disjoint value [5](#0-4) . There is an existing unit test that explicitly validates non-overlap: `test_delete_account_burns_gas_key_balances` funds three gas keys, deletes the account, and asserts `tokens_burnt` equals exactly the sum of gas key deposits, with no leftover mismatch [6](#0-5) .

Regarding the TOCTOU/race sub-claims: `compute_gas_key_balance_sum` and `remove_account` are both called synchronously within the same `action_delete_account` invocation on the same `&mut TrieUpdate`, with no intervening action execution — actions within a receipt are applied strictly sequentially by the runtime's single-threaded action loop [7](#0-6) , so an `AddKey`/`WithdrawFromGasKey` earlier in the same batch is already reflected in the trie before `DeleteAccount`'s reads happen — there's no reachable window for a stale read as hypothesized. Likewise, "concurrent" gas-refund receipts execute one receipt at a time within a chunk apply; genuine concurrency between receipts targeting the same account/key does not exist in this codebase's execution model, making that scenario non-reachable as well.

None of the sub-questions identify a concrete, reachable path where the disjoint `amount()` and gas-key-balance ledgers get summed incorrectly or double-counted.

### Citations

**File:** core/primitives-core/src/account.rs (L810-813)
```rust
pub struct GasKeyInfo {
    pub balance: Balance,
    pub num_nonces: NonceIndex,
}
```

**File:** runtime/runtime/src/access_keys.rs (L257-288)
```rust
pub(crate) fn action_transfer_to_gas_key(
    state_update: &mut TrieUpdate,
    result: &mut ActionResult,
    account_id: &AccountId,
    action: &TransferToGasKeyAction,
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

    gas_key_info.balance = gas_key_info.balance.checked_add(action.deposit).ok_or_else(|| {
        RuntimeError::StorageError(StorageError::StorageInconsistentState(
            "gas key balance integer overflow".to_string(),
        ))
    })?;
    set_access_key(state_update, account_id.clone(), action.public_key.clone(), &access_key);
    Ok(())
}
```

**File:** runtime/runtime/src/access_keys.rs (L315-333)
```rust
    let Some(updated_balance) = gas_key_info.balance.checked_sub(action.amount) else {
        result.result = Err(ActionErrorKind::InsufficientGasKeyBalance {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
            balance: gas_key_info.balance,
            required: action.amount,
        }
        .into());
        return Ok(());
    };
    gas_key_info.balance = updated_balance;
    set_access_key(state_update, account_id.clone(), action.public_key.clone(), &access_key);

    let new_account_balance = account.amount().checked_add(action.amount).ok_or_else(|| {
        RuntimeError::StorageError(StorageError::StorageInconsistentState(
            "Account balance integer overflow".to_string(),
        ))
    })?;
    account.set_amount(new_account_balance);
```

**File:** runtime/runtime/src/access_keys.rs (L715-756)
```rust
    #[test]
    fn test_delete_account_burns_gas_key_balances() {
        let (account_id, public_key, access_key) = test_account_keys();
        let public_keys: Vec<PublicKey> = (0..3)
            .map(|i| PublicKey::from_seed(KeyType::ED25519, &format!("gas_key_{i}")))
            .collect();
        let mut state_update = setup_account(&account_id, &public_key, &access_key);
        let mut account = get_account(&state_update, &account_id).unwrap().unwrap();
        for public_key in &public_keys {
            add_gas_key_to_account(&mut state_update, &mut account, &account_id, public_key);
        }

        // Fund each gas key with different amounts
        let deposit_amounts = [
            Balance::from_yoctonear(100_000),
            Balance::from_yoctonear(200_000),
            Balance::from_yoctonear(300_000),
        ];
        for (public_key, amount) in public_keys.iter().zip(deposit_amounts.iter()) {
            transfer_to_gas_key(&mut state_update, &account_id, public_key, *amount);
        }
        state_update.commit(StateChangeCause::InitialState);

        let action_result = test_delete_account(
            &account_id,
            AccountContract::from_local_code_hash(CryptoHash::default()),
            100,
            PROTOCOL_VERSION,
            &mut state_update,
        );
        assert!(action_result.result.is_ok());

        // Verify total burned balance equals sum of all gas key balances
        let expected_burnt =
            deposit_amounts.iter().fold(Balance::ZERO, |acc, x| acc.checked_add(*x).unwrap());
        assert_eq!(action_result.tokens_burnt, expected_burnt);
        let expected_compute: u64 = public_keys
            .iter()
            .map(|pk| expected_nonce_remove_compute(&account_id, pk, TEST_NUM_NONCES as usize))
            .sum();
        assert_eq!(action_result.compute_usage, expected_compute);
    }
```

**File:** core/store/src/utils/mod.rs (L457-497)
```rust
/// Computes the total balance across all gas keys for a given account.
pub fn compute_gas_key_balance_sum(
    state_update: &TrieUpdate,
    account_id: &AccountId,
) -> Result<Balance, StorageError> {
    let mut total = Balance::ZERO;
    let lock = state_update.trie().lock_for_iter();
    for raw_key in state_update
        .locked_iter(&trie_key_parsers::get_raw_prefix_for_access_keys(account_id), &lock)?
    {
        let raw_key = raw_key?;
        let key_handle = trie_key_parsers::parse_key_handle_from_access_key_key(
            &raw_key, account_id,
        )
        .map_err(|_e| {
            StorageError::StorageInconsistentState(
                "Can't parse key handle from raw key for AccessKey".to_string(),
            )
        })?;
        let nonce_index =
            trie_key_parsers::parse_nonce_index_from_gas_key_key(&raw_key, account_id, &key_handle)
                .map_err(|_e| {
                    StorageError::StorageInconsistentState(
                        "Can't parse nonce index from raw key for AccessKey".to_string(),
                    )
                })?;
        if nonce_index.is_some() {
            continue;
        }
        if let Some(balance) = get_access_key_by_handle(state_update, account_id, &key_handle)?
            .as_ref()
            .and_then(|access_key| access_key.gas_key_info())
            .map(|gas_key_info| gas_key_info.balance)
        {
            total = total.checked_add(balance).ok_or_else(|| {
                StorageError::StorageInconsistentState("gas key balance overflow".to_string())
            })?;
        }
    }
    Ok(total)
}
```

**File:** runtime/runtime/src/actions.rs (L354-371)
```rust
    let gas_key_balance_to_burn = compute_gas_key_balance_sum(state_update, account_id)?;
    if gas_key_balance_to_burn > GasKeyInfo::MAX_BALANCE_TO_BURN {
        result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh {
            account_id: account_id.clone(),
            public_key: None,
            balance: gas_key_balance_to_burn,
        }
        .into());
        return Ok(());
    }
    // We use current amount as a pay out to beneficiary.
    let account_balance = account_ref.amount();
    if account_balance > Balance::ZERO {
        result
            .new_receipts
            .push(Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance));
    }
    let remove_result = remove_account(state_update, account_id)?;
```
