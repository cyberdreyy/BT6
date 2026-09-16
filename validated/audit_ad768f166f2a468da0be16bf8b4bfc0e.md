### Title
Panic-on-overflow in deposit-transaction balance deduction can halt the node - ([File: crates/common/evm/src/handler.rs])

### Summary
`BaseHandler::validate_against_state_and_deduct_caller` computes a deposit transaction's balance deduction using `tx.effective_balance_spending(basefee, blob_price).expect("Deposit transaction effective balance spending overflow")`. Unlike every other arithmetic path in this file and its EVM2/EIP-8130 counterparts (which use `checked_add`/`checked_sub`/`saturating_*` and return a typed error), this call `.expect()`s the `Option` returned by the underlying revm trait method and panics the process if it is `None`.

### Finding Description
`effective_balance_spending` is the revm `Transaction` trait's helper that combines the transaction's max fee/gas-limit cost with `tx.value()`. For deposit transactions, `tx.value()` and other opaque fields are decoded directly from an L1 `TransactionDeposited` event log, entirely attacker-controlled data, as seen in `Deposits::unmarshal_v0` (`crates/consensus/protocol/src/deposits.rs:219-267`), which reads `mint` (u128), `value` (U256), and `gas_limit` (u64) straight from log bytes with no upper-bound sanity check beyond type width. Any L1 account can call `OptimismPortal.depositTransaction` (or the equivalent) with a `value` near `U256::MAX` and a large `gas_limit`/`max_fee_per_gas`, producing an opaque `TransactionDeposited` log that the derivation pipeline decodes into a `TxDeposit` and hands to the L2 execution engine.

When this deposit reaches `validate_against_state_and_deduct_caller` [1](#0-0) 
the code does:
```
let effective_balance_spending = tx
    .effective_balance_spending(basefee, blob_price)
    .expect("Deposit transaction effective balance spending overflow")
    - tx.value();
```
If the internal `gas_limit * fee + value` addition overflows `U256`/the trait's checked arithmetic returns `None`, the `.expect()` panics rather than gracefully rejecting or saturating. Since deposit transactions "cannot fail" per the OP-stack spec (they are meant to always be included, per the comment at `crates/common/evm2/src/registry.rs:98-101`), the code path has no error-return mechanism analogous to the checked-arithmetic style used everywhere else in the file (e.g., `chain.tx_cost_with_tx` returning `Some`/`None` handled via a proper `Err`, `checked_sub` with a returned `InvalidTransaction` error at lines 165-171 of the same function). This is the same underlying bug class as the report's `KeeperRewards.canUpdateRewards()` finding — reliance on an arithmetic operation that is assumed "safe" by inline comment/convention but is not actually guarded against attacker-supplied inputs — except here the consequence is a Rust panic rather than a silent wraparound.

### Impact Explanation
A panic inside transaction execution on this hot path (invoked for every deposit transaction, i.e., every L2 block that includes an L1 attributes/user deposit) will abort the node process (or at minimum the execution thread/task, depending on panic-handling configuration), since there is no `catch_unwind` visible around block execution in this handler. This can be triggered purely by an L1 depositor crafting the extreme-value deposit described above — matching the allowed "derivation of attacker-written L1 deposit ... data" and "node halt" categories in scope. If exploitable, this would cause the sequencer/validator nodes processing that L1 block to crash, halting chain progress (a Medium/High severity availability issue), rather than merely returning a wrong value as in the original StakeWise report.

### Likelihood Explanation
Likelihood depends on whether revm's `effective_balance_spending` implementation can actually return `None` for values reachable through the deposit fields' natural ranges (`value: U256` up to `U256::MAX`, `gas_limit: u64::MAX`, and a `max_fee_per_gas`/`basefee` derived from block state). Because `value` is a full `U256` decoded from arbitrary L1 log bytes with no bound check in `Deposits::unmarshal_v0`, and the addition combines it with a gas-cost term, an attacker can choose `value` close to `U256::MAX` to force the internal addition to overflow. I could not fully verify the exact body of `effective_balance_spending` (it lives in the external `revm` crate, not in this repository), so I cannot conclusively prove the overflow is reachable without also inspecting that dependency's source, this is the primary uncertainty in this finding. The `.expect()` panic message itself ("Deposit transaction effective balance spending overflow") is written as if the author expected this could theoretically trigger, matching the acknowledged nature of the analogous StakeWise finding.

### Recommendation
Replace the `.expect()` with an explicit checked path that treats overflow as a failed/rejected deposit (consistent with how other deposit failure modes are handled, e.g., `BaseEvmTypes::failed_deposit` in `crates/common/evm2/src/registry.rs`) instead of panicking the process. At minimum, saturate or clamp the computed spending to `U256::MAX` and log/telemetry the anomaly, or propagate a typed `BaseTransactionError` so the deposit is settled as a failed deposit rather than aborting execution.

### Proof of Concept
1. On L1, call the deposit contract with `value` set to a value very close to `U256::MAX` (e.g., `U256::MAX - 1`) and a large `gas_limit`/implied fee, producing a `TransactionDeposited` event.
2. The derivation pipeline decodes this log via `Deposits::unmarshal_v0` (`crates/consensus/protocol/src/deposits.rs:219-267`) into a `TxDeposit` with `value` at that extreme magnitude, with no bounds validation performed.
3. When the L2 execution engine processes this deposit, `BaseHandler::validate_against_state_and_deduct_caller` (`crates/common/evm/src/handler.rs:104-143`) calls `tx.effective_balance_spending(basefee, blob_price)`, whose internal fee+value addition overflows `U256`, causing the wrapped `Option` to be `None`.
4. `.expect("Deposit transaction effective balance spending overflow")` panics, aborting execution of that block on every node that processes it. [1](#0-0) [2](#0-1)

### Citations

**File:** crates/common/evm/src/handler.rs (L112-128)
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

            // Mind value should be added first before subtracting the effective balance spending.
            let mut new_balance = caller
                .balance()
                .saturating_add(U256::from(tx.mint().unwrap_or_default()))
                .saturating_sub(effective_balance_spending);
```

**File:** crates/consensus/protocol/src/deposits.rs (L219-267)
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
        offset += 8;

        // uint8 isCreation
        // isCreation: If the boolean byte is 1 then dep.To will stay nil,
        // and it will create a contract using L2 account nonce to determine the created address.
        if data[offset] == 0 {
            tx.to = TxKind::Call(to);
        } else {
            tx.to = TxKind::Create;
        }
        offset += 1;

        // The remainder of the opaqueData is the transaction data (without length prefix).
        // The data may be padded to a multiple of 32 bytes
        let tx_data_len = data.len() - offset;

        // Remaining bytes fill the data
        tx.input = Bytes::copy_from_slice(&data[offset..offset + tx_data_len]);

        Ok(())
    }
```
