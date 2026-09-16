No vulnerability found for this question.

The reported bug class—crediting a depositor with the nominal transfer amount while the contract actually receives less due to fee-on-transfer token deduction—requires an external token contract call (`transferFrom`) whose receipt amount can diverge from the requested amount. In this codebase, the analogous token primitives are the B-20 precompiles (`AssetV1`/`AssetV2`/`StablecoinV1`/`StablecoinV2`), where `transfer`/`transferFrom` operate purely on internal ledger state via `move_balance`/`transfer_inner`, directly debiting and crediting balances with `checked_sub`/`checked_add` rather than making an external call to pull in a separate token whose received amount could differ from the requested amount. [1](#0-0) [2](#0-1) 

Likewise, L1→L2 ETH deposits (`TxDeposit`) mint the exact `mint` value carried in the L1 `TransactionDeposited` event directly into the sender's balance; this is not the result of pulling an external fee-on-transfer token, so there is no discrepancy between "amount requested" and "amount received" to exploit. [3](#0-2) 

There is no code path in this repo where the system calls an external ERC20's `transferFrom` and then credits an internal balance based on the pre-transfer amount rather than the actual balance delta received, so this bug class has no reachable analog here.

### Citations

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L100-123)
```rust
    fn move_balance<S: AssetAccounting, A: PolicyAccounting>(
        &self,
        token: &mut B20AssetToken<S, A>,
        from: Address,
        to: Address,
        amount: U256,
    ) -> Result<()> {
        let from_balance = token.accounting().balance_of(from)?;
        if from_balance < amount {
            return Err(BasePrecompileError::revert(IB20::InsufficientBalance {
                sender: from,
                balance: from_balance,
                needed: amount,
            }));
        }
        let new_from_balance =
            from_balance.checked_sub(amount).ok_or_else(BasePrecompileError::under_overflow)?;
        token.accounting_mut().set_balance(from, new_from_balance)?;
        let to_balance = token.accounting().balance_of(to)?;
        let new_to_balance =
            to_balance.checked_add(amount).ok_or_else(BasePrecompileError::under_overflow)?;
        token.accounting_mut().set_balance(to, new_to_balance)?;
        token.accounting_mut().emit_event(IB20::Transfer { from, to, amount }.encode_log_data())
    }
```

**File:** crates/common/precompiles/src/b20_asset/logic/v1.rs (L212-245)
```rust
    fn transfer_from(
        &self,
        token: &mut B20AssetToken<S, A>,
        caller: Address,
        from: Address,
        to: Address,
        amount: U256,
        privileged: bool,
    ) -> Result<()> {
        B20Guards::ensure_not_paused(token, IB20::PausableFeature::TRANSFER)?;
        if to == Address::ZERO {
            return Err(BasePrecompileError::revert(IB20::InvalidReceiver { receiver: to }));
        }
        if from == Address::ZERO {
            return Err(BasePrecompileError::revert(IB20::InvalidSender { sender: from }));
        }
        let allowance = token.accounting().allowance(from, caller)?;
        let is_infinite = allowance == U256::MAX;
        if !is_infinite && allowance < amount {
            return Err(BasePrecompileError::revert(IB20::InsufficientAllowance {
                spender: caller,
                allowance,
                needed: amount,
            }));
        }
        if !privileged && caller != from {
            B20Guards::ensure_policy_type(token, B20PolicyType::TransferExecutor, caller)?;
        }
        self.transfer_inner(token, from, to, amount, privileged)?;
        if is_infinite {
            return Ok(());
        }
        token.accounting_mut().set_allowance(from, caller, allowance - amount)
    }
```

**File:** crates/common/evm2/src/registry.rs (L68-97)
```rust
    pub fn handle_deposit(
        req: TxRequest<'_, '_, Self, TxDeposit>,
    ) -> HandlerResult<TxResult<Self>> {
        let host = req.host;
        let tx: &TxDeposit = *req.tx;

        // Credit the mint and bump the sender nonce, capturing the pre-bump nonce for a create
        // deposit's contract-address derivation.
        let nonce = Self::prepare_deposit_sender(host, tx)?;

        // System-transaction deposits are rejected post-Regolith (always active on Base).
        if tx.is_system_transaction {
            return Ok(Self::failed_deposit(tx));
        }

        // Pre-warm the sender, destination, coinbase, and precompiles, matching the standard
        // transaction handlers so warm/cold (EIP-2929) access gas agrees with the reference.
        warm_base_accounts(host, tx.from, tx.to);

        // Meter the deposit like a standard transaction: charge intrinsic gas up front, then
        // run the call/create frame with the remaining gas. A deposit that cannot afford its
        // intrinsic cost is settled as a failed deposit.
        let intrinsic = intrinsic_gas(host.version(), tx.from, tx.to, &tx.input, 0, 0, tx.value);
        if tx.gas_limit < intrinsic {
            return Ok(Self::failed_deposit(tx));
        }
        let (execution_gas_limit, reservoir) =
            initial_gas_and_reservoir(host.version(), tx.gas_limit, intrinsic, 0);
        let mut tx_gas =
            GasTracker::new_with_execution_gas_and_reservoir(execution_gas_limit, reservoir);
```
