## Analysis: Aptos-native analog found

The external report's core invariant is: **a withdrawal/distribution path that uses a "safe" transfer primitive can be permanently DoS'd by the recipient's own inability/refusal to accept the asset, and this failure aborts the whole operation rather than being isolated to that one recipient.**

The Aptos analog exists in `vesting::distribute` (and the structurally identical pattern in `staking_contract::distribute_internal`), which pay out *all* shareholders of a shared distribution pool in a single loop inside one atomic transaction, using `aptos_account::deposit_coins`, which reverts if the recipient is unregistered for `AptosCoin` **and** has disabled direct coin transfers.

### Title
Single griefing shareholder can permanently block `vesting::distribute`/`staking_contract` commission and payout distribution for all other shareholders and the operator - (File: `aptos-move/framework/aptos-framework/sources/vesting.move`)

### Summary
`vesting::distribute` iterates over every shareholder of the vesting contract's `grant_pool` in one transaction and calls `aptos_account::deposit_coins` for each one [1](#0-0) . `aptos_account::deposit_coins` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the recipient is not registered for `AptosCoin` and has disabled `allow_arbitrary_coin_transfers` [2](#0-1) , a flag any account can set for itself via `set_allow_direct_coin_transfers(account, false)` [3](#0-2) . Because the loop processes all shareholders in a single Move transaction, one shareholder deliberately making itself unable to receive direct coins causes the entire `distribute()` call to abort — including the payouts owed to every *other* shareholder and to the withdrawal address's dust remainder. The same `aptos_account::deposit_coins` pattern is used for the "dust" fallback in `staking_contract::distribute_internal`, which pays out staker/operator commission from a shared `distribution_pool` [4](#0-3) .

### Finding Description
- `vesting::distribute` withdraws all currently-withdrawable stake, then loops `grant_pool.shareholders()` computing each shareholder's proportional share and calling `aptos_account::deposit_coins(recipient_address, share_of_coins)` for the (possibly beneficiary-redirected) recipient [5](#0-4) .
- `deposit_coins` only auto-registers the recipient's `CoinStore` if `can_receive_direct_coin_transfers(to)` is true; otherwise it aborts [2](#0-1) .
- Any account (including a vesting shareholder or a beneficiary set for that shareholder) can call `aptos_account::set_allow_direct_coin_transfers(signer, false)` and simply never call `coin::register<AptosCoin>()`, making itself permanently un-payable via `deposit_coins` [3](#0-2) .
- Because the `for_each_ref` loop over shareholders executes in one atomic transaction, an abort on any single shareholder's payout reverts the entire `distribute()` transaction — the operator's commission and every other shareholder's already-vested/rewarded funds that were about to be paid out this call are blocked as a side effect, exactly like the ERC721 case where one recipient's inability to receive traps assets meant for that recipient (and, in the Aptos case, additionally traps unrelated third parties' funds due to the shared-loop atomicity).
- `staking_contract::distribute_internal` follows the same recipient-payout-then-shared-pool pattern; its final "dust to staker" step already uses `aptos_account::deposit_coins`, confirming the same `deposit_coins` semantics govern payouts from the shared `distribution_pool` used by both the staker and operator/beneficiary [4](#0-3) .

### Impact Explanation
Under the Required Impacts gate, this credits/traps value belonging to parties other than the attacker: a malicious vesting shareholder (or a shareholder who set a hostile `beneficiary`) can indefinitely block `distribute()` for the whole vesting contract, meaning the operator's commission and every co-shareholder's vested/reward payout is trapped in the contract until the griefer chooses to re-enable direct transfers or register a `CoinStore` — something entirely outside the control of the operator or other shareholders. This is a share-accounting/payout DoS that traps commission and vested balances belonging to unrelated, non-consenting accounts, satisfying "Operator commission, beneficiary payout... corruption that... traps value."

### Likelihood Explanation
Moderate-to-high: no privileged role is required to trigger the block — any of the vesting contract's designated shareholders (or an account they've set as their beneficiary) can call the standard, permissionless `aptos_account::set_allow_direct_coin_transfers(false)` and avoid registering a `CoinStore`. The condition is trivially reproducible and persists across every subsequent `distribute()`/`vest()`/`distribute_many()` call as long as the griefer maintains the state, which is fully within their control.

### Recommendation
- In `vesting::distribute` (and `staking_contract::distribute_internal`), do not let one recipient's failed deposit abort payouts to all other shareholders. Wrap each `aptos_account::deposit_coins` call so a failure is caught/isolated (e.g., check `coin::is_account_registered` / `can_receive_direct_coin_transfers` first and, if the recipient cannot receive funds, retain their share in an escrow/claimable balance under their own address instead of aborting the whole loop) — analogous to the external report's fix of avoiding a "safe" call that can revert the whole transaction due to recipient-controlled state, and instead ensuring other legitimate recipients aren't penalized by one recipient's non-acceptance.

### Proof of Concept
1. Admin creates a vesting contract with shareholders `[S1, S2]` and sets `operator` with nonzero `commission_percentage` via `vesting::create_vesting_contract`.
2. `S1` calls `aptos_account::set_allow_direct_coin_transfers(S1_signer, false)` and never calls `coin::register<AptosCoin>()`.
3. Time passes; `vest()`/`unlock_rewards()` accrue vested amounts and commission in the underlying `staking_contract`.
4. Anyone calls `vesting::distribute(contract_address)`. The shareholder loop reaches `S1`, calls `aptos_account::deposit_coins(S1, share_of_coins)`, which aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` per `aptos_account::deposit_coins` [2](#0-1) .
5. The entire `distribute()` transaction reverts — `S2`'s vested funds and the operator's already-unlocked commission (paid out from the same `distribution_pool`/`grant_pool` withdrawal within this call) remain undistributed indefinitely, even though neither `S2` nor the operator did anything wrong, for as long as `S1` keeps direct transfers disabled. [6](#0-5) [3](#0-2) 

**Uncertainty:** I could not fully view the shareholder-iteration loop of `staking_contract::distribute_internal` (lines roughly 840–901) due to a display/truncation artifact in the retrieved snippet, so I am not 100% certain whether it uses `aptos_account::deposit_coins` (griefable) or a stricter `coin::deposit` for the primary per-shareholder payout — only the trailing "dust to staker" fallback at lines 913–919 was fully visible and confirmed to use `aptos_account::deposit_coins`. The `vesting::distribute` path, which is fully confirmed, is sufficient on its own to establish the vulnerability class described above.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L718-756)
```text
    /// Distribute any withdrawable stake from the stake pool.
    public entry fun distribute(contract_address: address) acquires VestingContract {
        assert_active_vesting_contract(contract_address);

        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        let coins = withdraw_stake(vesting_contract, contract_address);
        let total_distribution_amount = coin::value(&coins);
        if (total_distribution_amount == 0) {
            coin::destroy_zero(coins);
            return
        };

        // Distribute coins to all shareholders in the vesting contract.
        let grant_pool = &vesting_contract.grant_pool;
        let shareholders = &grant_pool.shareholders();
        shareholders.for_each_ref(|shareholder| {
            let shareholder = *shareholder;
            let shares = pool_u64::shares(grant_pool, shareholder);
            let amount = pool_u64::shares_to_amount_with_total_coins(grant_pool, shares, total_distribution_amount);
            let share_of_coins = coin::extract(&mut coins, amount);
            let recipient_address = get_beneficiary(vesting_contract, shareholder);
            aptos_account::deposit_coins(recipient_address, share_of_coins);
        });

        // Send any remaining "dust" (leftover due to rounding error) to the withdrawal address.
        if (coin::value(&coins) > 0) {
            aptos_account::deposit_coins(vesting_contract.withdrawal_address, coins);
        } else {
            coin::destroy_zero(coins);
        };

        emit(
            Distribute {
                admin: vesting_contract.admin,
                vesting_contract_address: contract_address,
                amount: total_distribution_amount,
            },
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L121-130)
```text
            };
        };
        if (!coin::is_account_registered<CoinType>(to)) {
            assert!(
                can_receive_direct_coin_transfers(to),
                error::permission_denied(EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS)
            );
            coin::register<CoinType>(&create_signer(to));
        };
        coin::deposit<CoinType>(to, coins)
```

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L187-211)
```text
    /// Set whether `account` can receive direct transfers of coins that they have not explicitly registered to receive.
    public entry fun set_allow_direct_coin_transfers(
        account: &signer, allow: bool
    ) acquires DirectTransferConfig {
        let addr = signer::address_of(account);
        if (exists<DirectTransferConfig>(addr)) {
            let direct_transfer_config = borrow_global_mut<DirectTransferConfig>(addr);
            // Short-circuit to avoid emitting an event if direct transfer config is not changing.
            if (direct_transfer_config.allow_arbitrary_coin_transfers == allow) { return };

            direct_transfer_config.allow_arbitrary_coin_transfers = allow;

            emit(
                DirectCoinTransferConfigUpdated {
                    account: addr,
                    new_allow_direct_transfers: allow
                }
            );
        } else {
            let direct_transfer_config = DirectTransferConfig {
                allow_arbitrary_coin_transfers: allow,
                update_coin_transfer_events: new_event_handle<
                    DirectCoinTransferConfigUpdatedEvent>(account)
            };

```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L913-919)
```text
        // In case there's any dust left, send them all to the staker.
        if (coin::value(&coins) > 0) {
            aptos_account::deposit_coins(staker, coins);
            distribution_pool.update_total_coins(0);
        } else {
            coin::destroy_zero(coins);
        }
```
