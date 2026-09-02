### Title
`internal_withdraw` in the staking pool debits ledger state before confirming the NEAR transfer succeeded, letting the on-chain ledger diverge from actual NEAR held - (File: `staking-pool/src/internal.rs`)

### Summary
The staking pool's `internal_withdraw` function mutates the user's `unstaked` balance and the contract-wide `last_total_balance` accounting *before* firing the outbound NEAR transfer, and never attaches a `.then()` callback to verify the transfer's `PromiseResult`. [1](#0-0)  This is the direct NEAR analog of the reported Solidity issue: the state-changing "transferFrom equivalent" is not checked for success, so bookkeeping proceeds as if the transfer definitely succeeded.

### Finding Description
`internal_withdraw` performs the following sequence unconditionally:
1. Decreases `account.unstaked` by `amount` and persists the account.
2. Decreases the contract's `last_total_balance` by `amount`.
3. Fires `Promise::new(account_id).transfer(amount)` with no attached callback. [2](#0-1) 

Contrast this with the rest of the same codebase, which consistently treats native/cross-contract transfers as fallible and reconciles ledger state only after confirming success via `is_promise_success()` in a `.then()` callback — e.g. `owner.rs`'s `withdraw_from_staking_pool` chains `ext_self_owner::on_staking_pool_withdraw` [3](#0-2)  which only finalizes the ledger update after checking `is_promise_success()` [4](#0-3) , and the foundation withdrawal flow does the same with `on_withdraw_unvested_amount` [5](#0-4) [6](#0-5) .

The staking pool's `withdraw` entrypoint, however, calls `internal_withdraw` directly and returns without any such confirmation step, breaking the equality: `unstaked balance claimed as withdrawn == NEAR actually delivered to the account`. If the outbound `Promise::new(account_id).transfer(amount)` fails (e.g., the receiving account no longer exists at execution time, or any other receipt-level failure), NEAR protocol semantics refund the deposit to the sending contract's own balance rather than reverting the receipt-triggering state changes on the staking pool contract (state changes made by `internal_withdraw` are not automatically rolled back because there is no callback to detect and act on failure). The user's `unstaked` balance and the contract's `last_total_balance` have already been permanently decremented, so:
- The user has no ledger record left to reclaim the refunded amount.
- The refunded NEAR sits in the staking pool contract's balance, unaccounted for in `last_total_balance`, i.e., the contract's recorded liabilities are now lower than the NEAR it actually holds — an accounting divergence that benefits nobody but permanently strands the user's funds.

### Impact Explanation
This is a High-impact accounting divergence: the ledger (`account.unstaked`, `last_total_balance`) is updated to reflect a successful withdrawal irrespective of whether the transfer promise actually succeeds, so the recorded claims no longer equal the NEAR the contract truly disburses. The affected user's funds become frozen with no code path to reclaim the amount debited from their account, and the contract's internal accounting silently understates the NEAR it holds relative to `last_total_balance`.

### Likelihood Explanation
This requires no privileged actor — any unprivileged user calling `withdraw` can trigger the vulnerable code path. The transfer-failure trigger condition (receipt-level transfer failure to the caller's own account) is an edge case rather than the common case, which is why this maps to a High/Medium likelihood rather than something trivially and reliably exploitable, but the missing safety check is systemic and unconditional in this code path, unlike the equivalent flows elsewhere in the same repository (lockup contract) which do check the promise result.

### Recommendation
Attach a `.then()` callback to the `Promise::new(account_id).transfer(amount)` call in `internal_withdraw`, check `is_promise_success()` in that callback, and only finalize (or conversely, roll back / re-credit) the `account.unstaked` and `last_total_balance` state changes based on the confirmed outcome of the transfer — mirroring the pattern already used in `lockup/src/owner_callbacks.rs::on_staking_pool_withdraw` and `lockup/src/foundation_callbacks.rs::on_withdraw_unvested_amount`.

### Proof of Concept
1. User calls `withdraw(amount)` on the staking pool; this invokes `internal_withdraw`. [7](#0-6) 
2. `account.unstaked -= amount` and `self.last_total_balance -= amount` are committed to storage immediately.
3. `Promise::new(account_id).transfer(amount)` is dispatched with no `.then()` callback attached.
4. If the transfer receipt fails for any reason, NEAR refunds the deposit to the staking pool contract's own balance rather than to the user, but the contract has no mechanism to detect this and re-credit `account.unstaked`.
5. Result: the user's recorded unstaked balance is permanently reduced despite never receiving the NEAR, while the contract's actual NEAR balance now exceeds what `last_total_balance` claims it holds — a divergence between recorded ledger state and real NEAR held, with the user's funds effectively frozen with no available reclaim path.

### Citations

**File:** staking-pool/src/internal.rs (L42-68)
```rust
    pub(crate) fn internal_withdraw(&mut self, amount: Balance) {
        assert!(amount > 0, "Withdrawal amount should be positive");

        let account_id = env::predecessor_account_id();
        let mut account = self.internal_get_account(&account_id);
        assert!(
            account.unstaked >= amount,
            "Not enough unstaked balance to withdraw"
        );
        assert!(
            account.unstaked_available_epoch_height <= env::epoch_height(),
            "The unstaked balance is not yet available due to unstaking delay"
        );
        account.unstaked -= amount;
        self.internal_save_account(&account_id, &account);

        env::log(
            format!(
                "@{} withdrawing {}. New unstaked balance is {}",
                account_id, amount, account.unstaked
            )
            .as_bytes(),
        );

        Promise::new(account_id).transfer(amount);
        self.last_total_balance -= amount;
    }
```

**File:** lockup/src/owner.rs (L236-251)
```rust
        ext_staking_pool::withdraw(
            amount,
            &self
                .staking_information
                .as_ref()
                .unwrap()
                .staking_pool_account_id,
            NO_DEPOSIT,
            gas::staking_pool::WITHDRAW,
        )
        .then(ext_self_owner::on_staking_pool_withdraw(
            amount,
            &env::current_account_id(),
            NO_DEPOSIT,
            gas::owner_callbacks::ON_STAKING_POOL_WITHDRAW,
        ))
```

**File:** lockup/src/owner_callbacks.rs (L105-119)
```rust
    pub fn on_staking_pool_withdraw(&mut self, amount: WrappedBalance) -> bool {
        assert_self();

        let withdraw_succeeded = is_promise_success();
        self.set_staking_pool_status(TransactionStatus::Idle);

        if withdraw_succeeded {
            {
                let staking_information = self.staking_information.as_mut().unwrap();
                // Due to staking rewards the deposit amount can become negative.
                staking_information.deposit_amount.0 = staking_information
                    .deposit_amount
                    .0
                    .saturating_sub(amount.0);
            }
```

**File:** lockup/src/foundation.rs (L165-174)
```rust
        Promise::new(receiver_id.clone()).transfer(amount).then(
            ext_self_foundation::on_withdraw_unvested_amount(
                amount.into(),
                receiver_id,
                &env::current_account_id(),
                NO_DEPOSIT,
                gas::foundation_callbacks::ON_WITHDRAW_UNVESTED_AMOUNT,
            ),
        )
    }
```

**File:** lockup/src/foundation_callbacks.rs (L188-230)
```rust
    pub fn on_withdraw_unvested_amount(
        &mut self,
        amount: WrappedBalance,
        receiver_id: AccountId,
    ) -> bool {
        assert_self();

        let withdraw_succeeded = is_promise_success();
        if withdraw_succeeded {
            env::log(
                format!(
                    "Termination Step: The withdrawal of the terminated unvested amount of {} to @{} succeeded.",
                    amount.0, receiver_id
                )
                    .as_bytes(),
            );
            // Decreasing lockup amount after withdrawal.
            self.lockup_information.termination_withdrawn_tokens += amount.0;
            let unvested_amount = self.get_terminated_unvested_balance().0;
            if unvested_amount > amount.0 {
                // There is still unvested balance remaining.
                let remaining_balance = unvested_amount - amount.0;
                self.vesting_information =
                    VestingInformation::Terminating(TerminationInformation {
                        unvested_amount: remaining_balance.into(),
                        status: TerminationStatus::ReadyToWithdraw,
                    });
                env::log(
                    format!(
                        "Termination Step: There is still terminated unvested balance of {} remaining to be withdrawn",
                        remaining_balance
                    )
                        .as_bytes(),
                );
                if self.get_account_balance().0 == 0 {
                    env::log(b"The withdrawal is completed: no more balance can be withdrawn in a future call");
                }
            } else {
                self.foundation_account_id = None;
                self.vesting_information = VestingInformation::None;
                env::log(b"Vesting schedule termination and withdrawal are completed");
            }
        } else {
```
