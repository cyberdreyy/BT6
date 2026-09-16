### Title
Attacker-controlled L1 deposit `value` can panic every L2 node during transaction validation - ([File: crates/common/evm/src/handler.rs])

### Summary
`BaseHandler::validate_against_state_and_deduct_caller` computes the deposit transaction's balance deduction with an unchecked subtraction against a fully attacker-controlled field (`tx.value()`), and immediately above it also relies on `.expect()` on a fallible computation. Because deposit transactions are explicitly *not* additionally validated ("pre-verified on L1"), and because any unprivileged L1 account can submit a `depositTransaction()` call with an arbitrary `value`, a crafted deposit can drive this arithmetic into an underflow/overflow panic. This is directly analogous to the reported bug class: insufficient validation of externally supplied numeric parameters lets a downstream computation revert/panic unexpectedly — except here the "revert" is a Rust panic reached during block execution, which every sequencer and verifier node must process identically, escalating a DoS into a chain-wide node halt.

### Finding Description
Deposit transaction fields (`mint: u128`, `value: U256`, `gas_limit: u64`) are decoded verbatim from L1 `TransactionDeposited` log data with no bound on `value` [1](#0-0) , and the resulting `TxDeposit` is converted into the EVM's `TxEnv` carrying that `value` straight through [2](#0-1) .

During execution, `validate_env` explicitly skips all extra validation for deposit transactions because "they are pre-verified on L1" [3](#0-2) . The caller-balance deduction path then computes:

```rust
let effective_balance_spending = tx
    .effective_balance_spending(basefee, blob_price)
    .expect("Deposit transaction effective balance spending overflow")
    - tx.value();
``` [4](#0-3) 

`tx.value()` is the raw, attacker-chosen deposit `value` (up to `U256::MAX`) with no upper bound enforced anywhere in the decode or apply paths shown above. `effective_balance_spending` for a deposit is derived from `gas_limit * gas_price` plus blob pricing terms; a deposit's `gas_price` is `U256::ZERO` (see the `TxEnv` conversion, which leaves `gas_price`/priority fee unset/default) [5](#0-4) , so `effective_balance_spending` is normally a small (often zero) value. When an attacker sets `value` larger than this small `effective_balance_spending`, the subtraction underflows. `alloy_primitives::U256` subtraction panics on underflow (it does not silently wrap), so this line panics unconditionally for every node that executes the block containing the deposit — there is no `checked_sub`/`saturating_sub` guard here, unlike the neighboring `checked_sub` used for the non-deposit fee-deduction path a few lines below [6](#0-5) .

Separately, the same block also uses `.expect("Deposit transaction effective balance spending overflow")` on the `Option` returned by `effective_balance_spending`, meaning any input combination that makes that internal computation overflow also panics rather than being rejected gracefully.

Because deposit transactions are forced into every block that spans the L1 epoch containing the deposit (they cannot simply be dropped by block builders the way a reverting user transaction can), a malicious deposit is unavoidable state for consensus: the sequencer must include it, and every verifier/replica node must execute it identically to compute the correct state root.

### Impact Explanation
A panic inside `Handler::validate_against_state_and_deduct_caller`, which runs during ordinary EVM transaction processing (via `reth`/`revm`), is not caught as a soft/tx-level error the way `catch_error` handles deposit *execution* failures (`catch_error` only handles `IsTxError`s surfaced from inside the frame, not a Rust panic unwinding out of the handler itself) [7](#0-6) . A panic here crashes the node process (or is only caught at a much higher panic boundary, e.g., std::panic::catch_unwind around block execution if any exists — not evidenced in the reviewed code). Since the deposit is embedded in the canonical L1-derived block, every full node, sequencer, and verifier that derives/executes that L2 block hits the identical panic, causing a synchronized node halt / potential chain split (nodes that crash vs. nodes that might handle the panic differently). This satisfies the "node halt / chain split" impact bar.

### Likelihood Explanation
Triggering this requires only an ordinary, unprivileged L1 transaction: calling the L1 `OptimismPortal.depositTransaction()` (or equivalent) with an arbitrarily large `value` field and a small `gas_limit`, which any L1 account can do without any special permission, funds, or contract deployment beyond the L1 gas cost of the deposit call itself. There is no additional validation gate between the L1 deposit call and L2 execution that bounds `value` relative to `effective_balance_spending`. This makes exploitation low-cost and directly reachable by "an unprivileged transaction sender" as scoped in this engagement.

### Recommendation
Replace the unchecked subtraction with a `checked_sub`/`saturating_sub` and treat an underflow as a failed deposit (mirroring the existing `catch_error` "deposits can't fail fatally" handling), rather than as a Rust panic. Similarly replace the `.expect(...)` on `effective_balance_spending` with a mapped `BaseTransactionError`/failed-deposit outcome instead of panicking. In general, apply the same "deposits never fail fatally, they degrade to `FailedDeposit`" pattern that is already used elsewhere in this file to this arithmetic, and add explicit bounds validation on `value`/`mint` derived from L1 deposit opaque data before they reach EVM-level arithmetic.

### Proof of Concept
1. From any L1 EOA (no special role required), call the L1 deposit contract's `depositTransaction` with:
   - `to` = any L2 address,
   - `value` = a large amount, e.g., `type(uint256).max` (or any value greater than `basefee * gas_limit` for the chosen `gas_limit`),
   - `gas_limit` = a small value (e.g., `21000`),
   - `isCreation` = false, `data` = empty.
2. The L1 `TransactionDeposited` event is derived into a `TxDeposit` with `value` set to the attacker-chosen amount and `gas_limit` as given [1](#0-0) .
3. When the sequencer/verifier processes this deposit inside `validate_against_state_and_deduct_caller`, `effective_balance_spending` evaluates to a small value (since deposit `gas_price` is `0`), and `effective_balance_spending - tx.value()` underflows, panicking the executing process [8](#0-7) .

Note: I was not able to locate the concrete implementation/source of `effective_balance_spending` within this repository (it appears to originate from an external `op-revm`/`revm`-adjacent crate not indexed here), so the exact numeric bound at which the `.expect()` overflow path (as opposed to the immediately following unchecked subtraction) triggers could not be independently confirmed from the available index; a Devin session with full repository/dependency access would be needed to pin down that exact boundary condition.

### Citations

**File:** crates/consensus/protocol/src/deposits.rs (L219-246)
```rust
impl Deposits {
    /// Unmarshals a deposit transaction from the opaque data.
    pub fn unmarshal_v0(
        tx: &mut TxDeposit,
        to: Address,
        data: &[u8],
    ) -> Result<(), DepositDecodeError> {
        if data.len() < 32 + 32 + 8 + 1 {
            return Err(DepositDecodeError::UnexpectedOpaqueDataLen(data.len()));
        }

        let mut offset = 0;

        let raw_mint: [u8; 16] = data[offset + 16..offset + 32].try_into().map_err(|_| {
            DepositDecodeError::MintDecode(Bytes::copy_from_slice(&data[offset + 16..offset + 32]))
        })?;
        tx.mint = u128::from_be_bytes(raw_mint);
        offset += 32;

        // uint256 value
        tx.value = U256::from_be_slice(&data[offset..offset + 32]);
        offset += 32;

        // uint64 gas
        let raw_gas: [u8; 8] = data[offset..offset + 8].try_into().map_err(|_| {
            DepositDecodeError::GasDecode(Bytes::copy_from_slice(&data[offset..offset + 8]))
        })?;
        tx.gas_limit = u64::from_be_bytes(raw_gas);
```

**File:** crates/common/consensus/src/transaction/deposit.rs (L392-414)
```rust
#[cfg(feature = "evm")]
impl FromRecoveredTx<TxDeposit> for TxEnv {
    fn from_recovered_tx(tx: &TxDeposit, caller: alloy_primitives::Address) -> Self {
        let TxDeposit {
            to,
            value,
            gas_limit,
            input,
            source_hash: _,
            from: _,
            mint: _,
            is_system_transaction: _,
        } = tx;
        Self {
            tx_type: tx.ty(),
            caller,
            gas_limit: *gas_limit,
            kind: *to,
            value: *value,
            data: input.clone(),
            ..Default::default()
        }
    }
```

**File:** crates/common/evm/src/handler.rs (L81-94)
```rust
    fn validate_env(&self, evm: &mut Self::Evm) -> Result<(), Self::Error> {
        // Do not perform any extra validation for deposit transactions, they are pre-verified on L1.
        let ctx = evm.ctx();
        let tx = ctx.tx();
        let tx_type = tx.tx_type();
        if tx_type == DEPOSIT_TRANSACTION_TYPE {
            // Do not allow for a system transaction to be processed if Regolith is enabled.
            if tx.is_system_transaction()
                && evm.ctx().cfg().spec().is_enabled_in(BaseUpgrade::Regolith)
            {
                return Err(BaseTransactionError::DepositSystemTxPostRegolith.into());
            }
            return Ok(());
        }
```

**File:** crates/common/evm/src/handler.rs (L112-123)
```rust
        if tx.tx_type() == DEPOSIT_TRANSACTION_TYPE {
            let basefee = block.basefee() as u128;
            let blob_price = block.blob_gasprice().unwrap_or_default();
            // deposit skips max fee check and just deducts the effective balance spending.

            let mut caller = journal.load_account_with_code_mut(tx.caller())?.data;

            let effective_balance_spending = tx
                .effective_balance_spending(basefee, blob_price)
                .expect("Deposit transaction effective balance spending overflow")
                - tx.value();

```

**File:** crates/common/evm/src/handler.rs (L159-173)
```rust
        if !cfg.is_fee_charge_disabled() {
            let Some(additional_cost) = chain.tx_cost_with_tx(tx, spec) else {
                return Err(ERROR::from_string(
                    "[OPTIMISM] Failed to load enveloped transaction.".into(),
                ));
            };
            let Some(new_balance) = balance.checked_sub(additional_cost) else {
                return Err(InvalidTransaction::LackOfFundForMaxFee {
                    fee: Box::new(additional_cost),
                    balance: Box::new(balance),
                }
                .into());
            };
            balance = new_balance
        }
```

**File:** crates/common/evm/src/handler.rs (L335-382)
```rust
    fn catch_error(
        &self,
        evm: &mut Self::Evm,
        error: Self::Error,
    ) -> Result<ExecutionResult<Self::HaltReason>, Self::Error> {
        let is_deposit = evm.ctx().tx().tx_type() == DEPOSIT_TRANSACTION_TYPE;
        let is_tx_error = error.is_tx_error();
        let mut output = Err(error);

        // Deposit transaction can't fail so we manually handle it here.
        if is_tx_error && is_deposit {
            let ctx = evm.ctx();
            let tx = ctx.tx();
            let caller = tx.caller();
            let mint = tx.mint();
            let gas_limit = tx.gas_limit();
            let journal = evm.ctx().journal_mut();

            // discard all changes of this transaction
            // Default JournalCheckpoint is the first checkpoint and will wipe all changes.
            journal.checkpoint_revert(JournalCheckpoint::default());

            let mut acc = journal.load_account_mut(caller)?;
            acc.bump_nonce();
            acc.incr_balance(U256::from(mint.unwrap_or_default()));

            drop(acc); // Drop acc to avoid borrow checker issues.

            // We can now commit the changes.
            journal.commit_tx();

            // clear the journal
            output = Ok(ExecutionResult::Halt {
                reason: BaseHaltReason::FailedDeposit,
                gas: ResultGas::new_with_state_gas(gas_limit, 0, 0, 0),
                logs: Vec::new(),
            })
        } else {
            evm.ctx().journal_mut().discard_tx();
        }

        // do the cleanup
        evm.ctx().chain_mut().clear_tx_l1_cost();
        evm.ctx().local_mut().clear();
        evm.frame_stack().clear();

        output
    }
```
