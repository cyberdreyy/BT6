No vulnerability found for this question.

**Rationale:**

This describes intended, existing protocol behavior rather than a novel bug. `validate_deploy_global_contract_action` at [1](#0-0)  only checks size, and `action_deploy_global_contract` at [2](#0-1)  stores whatever bytes were provided and burns the storage fee without invoking `prepare_contract`/`prepare_v3`. However, this is symmetric with the **local** `DeployContractAction` path: `validate_deploy_contract_action` performs the identical size-only check [3](#0-2) , and the integration test explicitly documents that "Deploying will succeed either way" regardless of whether the code passes `prepare_contract` [4](#0-3) . The protocol's own spec documents this as deliberate design: "Most of the errors ... are saved and reported when a FunctionCallAction is submitted. Deployment itself may only report errors relevant to itself" [5](#0-4) .

`RuntimeContractIdentifier::resolve` → `Contract::get_code` [6](#0-5)  and `RuntimeContractExt::get_code` [7](#0-6)  only fetch raw bytes; actual WASM validation happens lazily at `FunctionCall` time via `prepare_contract` [8](#0-7) , which is again identical for local and global contracts.

The gas/fee consequence for a caller of a broken contract (`UseGlobalContract` + `FunctionCall`) is the ordinary fee-burn model applicable to *any* failed receipt execution: burnt gas up to the point of failure is not refunded, only unspent gas is refunded per `refund_unspent_gas_and_deposits` [9](#0-8)  and the documented Refunds model [10](#0-9) . This is the same economic risk inherent to calling any contract that might not exist, might error, or might run out of gas — it is not an authorization bypass, theft, or freezing of a third party's funds: the consuming account signs and submits its own `UseGlobalContract`/`FunctionCall` transaction voluntarily, bearing the known risk that the referenced global-contract identifier's code may not execute successfully. There is no code path where one account's signature is used to move or burn another account's funds without their own transaction authorizing it.

Since this behavior is symmetric with pre-existing local-contract semantics, is explicitly documented as intended, and does not cause loss of funds without the affected account's own authorizing signature, it does not meet the bar for a valid finding under the stated impact categories (theft/freezing of funds, inflation, double-spend, authorization escalation, consensus divergence, or shard halt).

### Citations

**File:** runtime/runtime/src/action_validation.rs (L223-236)
```rust
/// Validates `DeployContractAction`. Checks that the given contract size doesn't exceed the limit.
fn validate_deploy_contract_action(
    limit_config: &LimitConfig,
    action: &DeployContractAction,
) -> Result<(), ActionsValidationError> {
    if action.code.len() as u64 > limit_config.max_contract_size {
        return Err(ActionsValidationError::ContractSizeExceeded {
            size: action.code.len() as u64,
            limit: limit_config.max_contract_size,
        });
    }

    Ok(())
}
```

**File:** runtime/runtime/src/action_validation.rs (L239-251)
```rust
fn validate_deploy_global_contract_action(
    limit_config: &LimitConfig,
    action: &DeployGlobalContractAction,
) -> Result<(), ActionsValidationError> {
    if action.code.len() as u64 > limit_config.max_contract_size {
        return Err(ActionsValidationError::ContractSizeExceeded {
            size: action.code.len() as u64,
            limit: limit_config.max_contract_size,
        });
    }

    Ok(())
}
```

**File:** runtime/runtime/src/global_contracts.rs (L24-62)
```rust
pub(crate) fn action_deploy_global_contract(
    state_update: &mut TrieUpdate,
    account: &mut Account,
    account_id: &AccountId,
    apply_state: &ApplyState,
    deploy_contract: &DeployGlobalContractAction,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let _span = tracing::debug_span!(target: "runtime", "action_deploy_global_contract").entered();

    let storage_cost = apply_state
        .config
        .fees
        .storage_usage_config
        .global_contract_storage_amount_per_byte
        .saturating_mul(deploy_contract.code.len() as u128);
    let Some(updated_balance) = account.amount().checked_sub(storage_cost) else {
        result.result = Err(ActionErrorKind::LackBalanceForState {
            account_id: account_id.clone(),
            amount: storage_cost,
        }
        .into());
        return Ok(());
    };
    result.tokens_burnt =
        result.tokens_burnt.checked_add(storage_cost).ok_or(IntegerOverflowError)?;
    account.set_amount(updated_balance);

    initiate_distribution(
        state_update,
        account_id.clone(),
        deploy_contract.code.clone(),
        &deploy_contract.deploy_mode,
        apply_state.shard_id,
        result,
    )?;

    Ok(())
}
```

**File:** integration-tests/src/tests/runtime/deployment.rs (L66-74)
```rust
    // Deploy contract
    let wasm_binary = near_test_contracts::sized_contract(contract_size as usize);
    // Run code through preparation for validation. (Deploying will succeed either way).
    near_vm_runner::prepare::prepare_contract(
        &wasm_binary,
        &config.wasm_config,
        config.wasm_config.vm_kind,
    )
    .unwrap();
```

**File:** docs/RuntimeSpec/Preparation.md (L31-33)
```markdown
Most of the errors that have occurred as part of validation, instrumentation, compilation, etc. are
saved and reported when a `FunctionCallAction` is submitted. Deployment itself may only report
errors relevant to itself, as described in the specification for [`DeployContractAction`].
```

**File:** runtime/runtime/src/contract_code.rs (L36-73)
```rust
    pub(crate) fn resolve(
        account_id: &AccountId,
        account_contract: AccountContract,
        state_update: &TrieUpdate,
        chain_id: &str,
        access: AccessOptions,
    ) -> Result<Self, StorageError> {
        let local_hash = match GlobalContractIdentifier::try_from(account_contract) {
            Ok(gci) => {
                let code_hash = gci.clone().hash(state_update, access)?;
                return Ok(RuntimeContractIdentifier::Global { code_hash, identifier: gci });
            }
            Err(ContractIsLocalError::NotDeployed) => return Ok(RuntimeContractIdentifier::None),
            Err(ContractIsLocalError::Deployed(local_hash)) => local_hash,
        };

        if account_id.get_account_type() == AccountType::EthImplicitAccount {
            // Accounts that look like eth implicit accounts and have existed prior to the
            // eth-implicit accounts protocol change (these accounts are discussed in the
            // description of #11606) may have something else deployed to them. Only return
            // something here if the accounts have a wallet contract hash. Otherwise use the
            // regular path to grab the deployed contract.
            if LegacyEthWallet::resolve(local_hash).is_some() {
                // ETH implicit wallet accounts use global contracts, including
                // those created in old protocol versions.
                let global_hash = eth_wallet_global_contract_hash(chain_id);
                return Ok(RuntimeContractIdentifier::Global {
                    code_hash: global_hash,
                    identifier: GlobalContractIdentifier::CodeHash(global_hash),
                });
            }
        }

        Ok(RuntimeContractIdentifier::AccountLocal {
            code_hash: local_hash,
            account_id: account_id.clone(),
        })
    }
```

**File:** runtime/runtime/src/ext.rs (L635-643)
```rust
    fn get_code(&self) -> Option<Arc<ContractCode>> {
        match &self.identifier {
            RuntimeContractIdentifier::None => Option::None,
            RuntimeContractIdentifier::AccountLocal { code_hash, .. }
            | RuntimeContractIdentifier::Global { code_hash, .. } => {
                self.storage.get(*code_hash).map(Arc::new)
            }
        }
    }
```

**File:** runtime/near-vm-runner/src/prepare.rs (L22-33)
```rust
pub fn prepare_contract(
    original_code: &[u8],
    config: &Config,
    kind: VMKind,
) -> Result<Vec<u8>, PrepareError> {
    let features = crate::features::WasmFeatures::new(config);
    if config.reftypes_bulk_memory || config.vm_kind == VMKind::Wasmtime {
        prepare_v3::prepare_contract(original_code, features, config, kind)
    } else {
        prepare_v2::prepare_contract(original_code, features, config, kind)
    }
}
```

**File:** runtime/runtime/src/lib.rs (L1230-1275)
```rust
    fn refund_unspent_gas_and_deposits(
        &self,
        gas_burn_price: Balance,
        gas_purchase_price: Balance,
        receipt: &Receipt,
        action_receipt: &VersionedActionReceipt,
        result: &mut ActionReceiptResult,
        config: &RuntimeConfig,
        created_account: bool,
        protocol_version: ProtocolVersion,
    ) -> Result<GasRefundResult, RuntimeError> {
        let total_deposit = total_deposit(&action_receipt.actions())?;
        let prepaid_gas = total_prepaid_gas(&action_receipt.actions())?
            .checked_add(total_prepaid_send_fees(config, &action_receipt.actions())?.gas)
            .ok_or(IntegerOverflowError)?;
        let prepaid_exec_gas =
            total_prepaid_exec_fees(config, &action_receipt.actions(), receipt.receiver_id())?
                .checked_add(config.fees.fee(ActionCosts::new_action_receipt).exec_fee())
                .ok_or(IntegerOverflowError)?;
        let deposit_refund = if result.result.is_err() { total_deposit } else { Balance::ZERO };
        let gross_gas_refund = if result.result.is_err() {
            prepaid_gas
                .checked_add(prepaid_exec_gas.gas)
                .ok_or(IntegerOverflowError)?
                .checked_sub(result.gas_burnt)
                .unwrap()
        } else {
            prepaid_gas
                .checked_add(prepaid_exec_gas.gas)
                .ok_or(IntegerOverflowError)?
                .checked_sub(result.gas_used)
                .unwrap()
        };

        // NEP-536 also adds a penalty to gas refund.
        let refund_penalty: Gas = config.fees.gas_penalty_for_gas_refund(gross_gas_refund);
        let penalty_gas_price = if ProtocolFeature::AccountCostIncrease.enabled(protocol_version) {
            gas_burn_price
        } else {
            gas_purchase_price
        };
        let refund_penalty_amount = safe_gas_to_balance(penalty_gas_price, refund_penalty)?;

        // Refund for the leftover gas that was not used by this receipt.
        let unused_gas_balance_refund = safe_gas_to_balance(gas_purchase_price, gross_gas_refund)?
            .saturating_sub(refund_penalty_amount);
```

**File:** docs/RuntimeSpec/Refunds.md (L27-42)
```markdown
## Gas Refunds

Gas refunds are generated when a receipt used the amount of gas lower than the attached amount of gas.

If the receipt execution succeeded, the gas amount is equal to `prepaid_gas + execution_gas - used_gas`.

If the receipt execution failed, the gas amount is equal to `prepaid_gas + execution_gas - burnt_gas`.

The difference between `burnt_gas` and `used_gas` is the `used_gas` also includes the fees and the prepaid gas of
newly generated receipts, e.g. from cross-contract calls in function calls actions.

From this unspent gas amount, the network charges a gas refund fee, starting with protocol version 78. The exact fee is calculated as `max(gas_refund_penalty * unspent_gas, min_gas_refund_penalty)`. As of version 78, `gas_refund_penalty` is 0% and `min_gas_refund_penalty` 0 Tgas, always resulting in a zero-cost fee. This is only a stepping stone to give projects time to adapt before the fee is taking effect. The plan is to increase this to 5% and 1 Tgas, as specified in [NEP-536](https://github.com/near/NEPs/blob/master/neps/nep-0536.md). There is no fixed timeline available for this.

Should the gas refund fee be equal or larger than the unspent gas, no refund will be produced.

If there is gas to refund left, the gas amount is converted to tokens by multiplying by the gas price at which the original transaction was generated.
```
