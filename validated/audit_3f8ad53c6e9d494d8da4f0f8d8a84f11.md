No vulnerability found for this question.

Analysis: The `promise_batch_action_function_call` / `promise_batch_action_function_call_weight` host functions in `runtime/near-vm-runner/src/logic/logic.rs` and the wasmtime equivalent in `runtime/near-vm-runner/src/wasmtime_runner/logic.rs` contain no re-derivation or enforcement of the original signer's `FunctionCallPermission.receiver_id`/`method_names`. They only check `is_view()`, deduct the contract's own balance, and append the action to the receipt via `ext.append_action_function_call_weight`. [1](#0-0) 

This is the correct and intended design, not a bypassable residual check. `FunctionCallPermission` is enforced exactly once, at the boundary where the *original signer's transaction or DelegateAction* is validated against the access key — in `verify_function_call_permission` in `runtime/runtime/src/verifier.rs` for direct transactions, and in the analogous logic in `runtime/runtime/src/actions.rs` for meta-transaction delegate actions. [2](#0-1) [3](#0-2) 

Once that first-hop check passes, the receipt is executed and the receiver contract's WASM code runs with full authority over its **own** account balance and cross-contract call capabilities — there is no protocol-level concept of "the promise inherits the caller's key restriction." Any promises the contract creates via `promise_batch_action_function_call` draw from the contract's own balance/allowance mechanisms (`deduct_balance`), not from the original signer's key permissions. This is exactly the invariant the question describes as expected ("a promise never carries privileges its creator lacked") — the contract itself, as an account, is not restricted by the caller's access key; only the initiating transaction/delegate action was. The test suite confirms `FunctionCallPermission` checks are scoped strictly to the initiating transaction/delegate action (e.g., `ReceiverMismatch`, `MethodNameMismatch` at the verifier/delegate-action level), not to any downstream promise creation. [4](#0-3) [5](#0-4) 

There is no stale/vestigial receiver-or-method check against the original signer's key that could be evaded on the second-hop promise — no such check exists at all in that code path, which is the correct and documented behavior, not a bypass.

### Citations

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3059-3121)
```rust
    pub fn promise_batch_action_function_call_weight(
        &mut self,
        promise_idx: u64,
        method_name_len: u64,
        method_name_ptr: u64,
        arguments_len: u64,
        arguments_ptr: u64,
        amount_ptr: u64,
        gas: u64,
        gas_weight: u64,
    ) -> Result<()> {
        self.result_state.gas_counter.pay_base(base)?;
        if self.context.is_view() {
            return Err(HostError::ProhibitedInView {
                method_name: "promise_batch_action_function_call".to_string(),
            }
            .into());
        }
        let amount = Balance::from_yoctonear(
            self.memory.get_u128(&mut self.result_state.gas_counter, amount_ptr)?,
        );
        let gas = Gas::from_gas(gas);
        let method_name = get_memory_or_register!(self, method_name_ptr, method_name_len)?;
        if method_name.is_empty() {
            return Err(HostError::EmptyMethodName.into());
        }
        let arguments = get_memory_or_register!(self, arguments_ptr, arguments_len)?;

        let (receipt_idx, sir) = self.promise_idx_to_receipt_idx_with_sir(promise_idx)?;

        let method_name = method_name.into_owned();
        let arguments = arguments.into_owned();
        // Input can't be large enough to overflow
        let num_bytes = method_name.len() as u64 + arguments.len() as u64;
        self.pay_action_base(ActionCosts::function_call_base, sir)?;
        self.pay_action_per_byte(ActionCosts::function_call_byte, num_bytes, sir)?;
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

**File:** runtime/runtime/src/verifier.rs (L166-207)
```rust
fn verify_function_call_permission(
    function_call_permission: &FunctionCallPermission,
    tx: &Transaction,
) -> Result<(), InvalidTxError> {
    if tx.actions().len() != 1 {
        return Err(InvalidTxError::InvalidAccessKeyError(
            InvalidAccessKeyError::RequiresFullAccess,
        ));
    }
    let Some(Action::FunctionCall(function_call)) = tx.actions().get(0) else {
        return Err(InvalidTxError::InvalidAccessKeyError(
            InvalidAccessKeyError::RequiresFullAccess,
        ));
    };
    if function_call.deposit > Balance::ZERO {
        return Err(InvalidTxError::InvalidAccessKeyError(
            InvalidAccessKeyError::DepositWithFunctionCall,
        ));
    }
    let tx_receiver = tx.receiver_id();
    let ak_receiver = &function_call_permission.receiver_id;
    if tx_receiver != ak_receiver {
        return Err(InvalidTxError::InvalidAccessKeyError(
            InvalidAccessKeyError::ReceiverMismatch {
                tx_receiver: tx_receiver.clone(),
                ak_receiver: ak_receiver.clone(),
            },
        ));
    }
    if !function_call_permission.method_names.is_empty()
        && function_call_permission
            .method_names
            .iter()
            .all(|method_name| &function_call.method_name != method_name)
    {
        return Err(InvalidTxError::InvalidAccessKeyError(
            InvalidAccessKeyError::MethodNameMismatch {
                method_name: function_call.method_name.clone(),
            },
        ));
    }
    Ok(())
```

**File:** runtime/runtime/src/actions.rs (L654-711)
```rust
    // The restriction of "function call" access keys:
    // the transaction must contain the only `FunctionCall` if "function call" access key is used
    if let Some(function_call_permission) = access_key.permission.function_call_permission() {
        if actions.len() != 1 {
            result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
                InvalidAccessKeyError::RequiresFullAccess,
            )
            .into());
            return Ok(());
        }
        if let Some(Action::FunctionCall(function_call)) = actions.get(0) {
            if function_call.deposit > Balance::ZERO {
                result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
                    InvalidAccessKeyError::DepositWithFunctionCall,
                )
                .into());
                // Before this fix, the missing early return allowed execution
                // to fall through to the receiver_id and method_name checks,
                // which could overwrite this error with a different one.
                if ProtocolFeature::FixDelegateActionDepositWithFunctionCallError
                    .enabled(apply_state.current_protocol_version)
                {
                    return Ok(());
                }
            }
            if delegate_action.receiver_id() != &function_call_permission.receiver_id {
                result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
                    InvalidAccessKeyError::ReceiverMismatch {
                        tx_receiver: delegate_action.receiver_id().clone(),
                        ak_receiver: function_call_permission.receiver_id.clone(),
                    },
                )
                .into());
                return Ok(());
            }
            if !function_call_permission.method_names.is_empty()
                && function_call_permission
                    .method_names
                    .iter()
                    .all(|method_name| &function_call.method_name != method_name)
            {
                result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
                    InvalidAccessKeyError::MethodNameMismatch {
                        method_name: function_call.method_name.clone(),
                    },
                )
                .into());
                return Ok(());
            }
        } else {
            // There should Action::FunctionCall when "function call" permission is used
            result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
                InvalidAccessKeyError::RequiresFullAccess,
            )
            .into());
            return Ok(());
        }
    };
```

**File:** integration-tests/src/tests/standard_cases/mod.rs (L1437-1471)
```rust
pub fn test_access_key_smart_contract_reject_contract_id(node: impl Node) {
    let access_key = AccessKey {
        nonce: 0,
        permission: AccessKeyPermission::FunctionCall(FunctionCallPermission {
            allowance: Some(FUNCTION_CALL_AMOUNT),
            receiver_id: bob_account().into(),
            method_names: vec![],
        }),
    };
    let mut node_user = node.user();
    let account_id = &node.account_id().unwrap();
    let signer2 = InMemorySigner::from_random("test".parse().unwrap(), KeyType::ED25519).into();
    add_access_key(&node, node_user.as_ref(), &access_key, &signer2);
    node_user.set_signer(Arc::new(signer2));

    let transaction_result = node_user.function_call(
        account_id.clone(),
        eve_dot_alice_account(),
        "run_test",
        vec![],
        Gas::from_teragas(100),
        Balance::ZERO,
    );

    assert_matches!(
        transaction_result,
        Ok(FinalExecutionOutcomeView {
            status: FinalExecutionStatus::Failure(TxExecutionError::InvalidTxError(
                InvalidTxError::InvalidAccessKeyError(
                    InvalidAccessKeyError::ReceiverMismatch { .. }
                )
            )),
            ..
        })
    );
```

**File:** integration-tests/src/tests/features/delegate_action.rs (L430-471)
```rust
/// Call a function in a meta tx where the user doesn't have the appropriate
/// access key, which must fail.
///
/// This is quite to fail, method restricted access keys can give restricted
/// access to a contract. If meta transactions can be used to circumvent this
/// check, then someone with an access key could impersonate the account in
/// unintended ways.
#[test]
fn meta_tx_fn_call_access_wrong_method() {
    let sender = bob_account();
    let relayer = alice_account();
    let receiver = carol_account();
    let signer = create_user_test_signer(&sender);

    let access_key_method_name = "log_something_else";
    let node = setup_with_access_key(
        &relayer,
        &receiver,
        &sender,
        signer.public_key(),
        INITIAL_ALLOWANCE,
        access_key_method_name,
    );

    let actions = vec![log_something_fn_call()];
    let tx_result = node.user().meta_tx(sender, receiver, relayer, actions).unwrap();
    // actual check has to be done in the receipt on the sender shard, not the
    // relayer, so let's check the receipt is present with the appropriate error
    let inner_status = &tx_result.receipts_outcome[0].outcome.status;
    assert!(
        matches!(
            inner_status,
            ExecutionStatusView::Failure(TxExecutionError::ActionError(ActionError {
                kind: ActionErrorKind::DelegateActionAccessKeyError(
                    InvalidAccessKeyError::MethodNameMismatch { .. }
                ),
                ..
            })),
        ),
        "expected MethodNameMismatch but found {inner_status:?}"
    );
}
```
