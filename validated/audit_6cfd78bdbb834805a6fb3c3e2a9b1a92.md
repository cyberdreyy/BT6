### Title
Operator/beneficiary can permanently block staker `unlock_stake`/`request_commission`/`switch_operator` in `staking_contract` by refusing direct coin transfers - ([File: aptos-move/framework/aptos-framework/sources/staking_contract.move])

### Summary
`staking_contract::distribute_internal` pays out **all** shareholders of a single, shared `distribution_pool` (both the staker's unlocked stake and the operator's/beneficiary's commission) in one loop that calls `aptos_account::deposit_coins` for each recipient. Because `deposit_coins` can abort for an unregistered recipient who has disabled arbitrary coin transfers, and because `distribute_internal` is invoked as a mandatory first step inside `unlock_stake`, `request_commission`, `switch_operator`, and `update_commision`, an operator (or its beneficiary) can indefinitely block the staker from ever completing these calls — even though the staker owns the funds and did nothing wrong.

### Finding Description
`aptos_account::deposit_coins` only auto-registers/creates a `CoinStore` for a recipient if it is not already registered; if it is unregistered it asserts `can_receive_direct_coin_transfers(to)`, which is controlled by the recipient's own `DirectTransferConfig` (settable via the standard, fully unprivileged `set_allow_direct_coin_transfers`): [1](#0-0) 

`staking_contract::distribute_internal` iterates the staking contract's single `distribution_pool` shareholder-by-shareholder, redeeming shares and calling `aptos_account::deposit_coins` for each recipient (remapping the operator's payout to its beneficiary): [2](#0-1) 

This same `distribution_pool` is shared between the staker's withdrawal distributions (added in `unlock_stake` via `add_distribution`) and the operator's commission distributions (added in `request_commission_internal`): [3](#0-2) [4](#0-3) 

Critically, `distribute_internal` is called unconditionally at the top of the staker-facing entry functions that are supposed to always succeed for the staker:
- `unlock_stake` (staker requesting withdrawal): [5](#0-4) 
- `request_commission`: [6](#0-5) 
- `switch_operator`: [7](#0-6) 
- `update_commision`: [8](#0-7) 

Because the `while` loop in `distribute_internal` has no try/skip semantics, a single un-payable shareholder aborts the entire transaction. An operator can:
1. Never register a `CoinStore<AptosCoin>` for itself (or its `set_beneficiary_for_operator` target, see `staking_contract.move:807-838`), and
2. Call `set_allow_direct_coin_transfers(false)` (a normal, unprivileged self-service call anyone can make on their own account).

Once the operator has ANY pending commission distribution recorded in the shared pool (which happens automatically the first time rewards accrue and commission is owed), every future call to `unlock_stake`, `request_commission`, `switch_operator`, or `update_commision` by the staker will revert, because `distribute_internal` will attempt to pay the operator/beneficiary and hit the `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` abort.

This is the direct analog of the Rocket Pool bug: a withdrawal/administration path that is supposed to "always succeed" for the fund owner is gated on a call into unrelated account state (here, another party's coin-acceptance configuration) that the fund owner does not control and cannot force to succeed.

### Impact Explanation
The staker permanently loses the ability to exercise unlock/withdraw, commission-request, and operator-switch rights on their own stake as long as the operator maintains the blocking configuration, which the operator fully controls and can sustain indefinitely at no cost. This matches the required impact category "Permanent lock or non-recoverable loss of claim rights in stake, delegation, commission, beneficiary, or vesting flows" — the staker's principal remains locked in the pool with no code path to bypass the shared distribution pool's all-or-nothing payout loop. It is a high-severity griefing vector usable by any operator against any staker they service.

### Likelihood Explanation
Likelihood is high: the operator role is a normal, permissionless participant a staker chooses when creating a staking contract (`create_staking_contract`), so no privileged access is required. The only prerequisites — never registering a `CoinStore<AptosCoin>` and calling `set_allow_direct_coin_transfers(false)` — are both ordinary, unprivileged operations available to any account, and are trivially set up before or immediately after entering into the staking contract with the staker.

### Recommendation
Do not let a single recipient's payout failure abort the entire `distribute_internal` loop. Use per-recipient fault isolation (e.g., Move's abort-catching patterns are unavailable, so instead separate the staker's distribution pool from the operator's commission pool, or fall back to crediting a claimable balance / skip-and-continue mechanism when a `deposit_coins` call would fail) so that one uncooperative shareholder cannot block payouts—and therefore `unlock_stake`/`request_commission`/`switch_operator`/`update_commision`—for all other parties.

### Proof of Concept
1. Staker calls `staking_contract::create_staking_contract(staker, operator, ..., commission_percentage=10)`.
2. Time passes, rewards accrue; staker or operator triggers `request_commission`, which via `request_commission_internal` records a pending distribution for `operator` (or its beneficiary) in the shared `distribution_pool` (`staking_contract.move:637-674`).
3. Operator (who never registered `CoinStore<AptosCoin>`) calls `aptos_account::set_allow_direct_coin_transfers(operator_signer, false)`.
4. Staker calls `staking_contract::unlock_stake(staker, operator, amount)`. Internally this calls `distribute_internal`, which iterates shareholders and attempts `aptos_account::deposit_coins(operator_or_beneficiary, coins)` — this aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`, reverting the entire `unlock_stake` transaction.
5. Staker's call to `unlock_stake`, `request_commission`, `switch_operator`, and `update_commision` will all revert identically for as long as the operator maintains this configuration — the staker cannot unlock or withdraw their stake through this contract.

Note: I could not fully verify `pool_u64::shareholders()` ordering/internal iteration semantics in this pass (file reads for `pool_u64.move` did not complete), but the loop and abort-propagation behavior of `distribute_internal` and `deposit_coins` are confirmed directly from the cited source lines, which is sufficient to establish the root cause and abort propagation described above.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L121-131)
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
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L566-601)
```text
    public entry fun update_commision(
        staker: &signer, operator: address, new_commission_percentage: u64
    ) acquires Store, BeneficiaryForOperator {
        assert!(
            new_commission_percentage >= 0 && new_commission_percentage <= 100,
            error::invalid_argument(EINVALID_COMMISSION_PERCENTAGE)
        );

        let staker_address = signer::address_of(staker);
        assert!(
            exists<Store>(staker_address),
            error::not_found(ENO_STAKING_CONTRACT_FOUND_FOR_STAKER)
        );

        let store = borrow_global_mut<Store>(staker_address);
        let staking_contract = store.staking_contracts.borrow_mut(&operator);
        distribute_internal(
            staker_address,
            operator,
            staking_contract,
        );
        request_commission_internal(
            operator,
            staking_contract,
        );
        let old_commission_percentage = staking_contract.commission_percentage;
        staking_contract.commission_percentage = new_commission_percentage;
        emit(
            UpdateCommission {
                staker: staker_address,
                operator,
                old_commission_percentage,
                new_commission_percentage
            }
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L607-635)
```text
    public entry fun request_commission(
        account: &signer, staker: address, operator: address
    ) acquires Store, BeneficiaryForOperator {
        let account_addr = signer::address_of(account);
        assert!(
            account_addr == staker
                || account_addr == operator
                || account_addr == beneficiary_for_operator(operator),
            error::unauthenticated(ENOT_STAKER_OR_OPERATOR_OR_BENEFICIARY)
        );
        assert_staking_contract_exists(staker, operator);

        let store = borrow_global_mut<Store>(staker);
        let staking_contract = store.staking_contracts.borrow_mut(&operator);
        // Short-circuit if zero commission.
        if (staking_contract.commission_percentage == 0) { return };

        // Force distribution of any already inactive stake.
        distribute_internal(
            staker,
            operator,
            staking_contract,
        );

        request_commission_internal(
            operator,
            staking_contract,
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L637-674)
```text
    fun request_commission_internal(
        operator: address,
        staking_contract: &mut StakingContract,
    ): u64 {
        // Unlock just the commission portion from the stake pool.
        let (total_active_stake, accumulated_rewards, commission_amount) =
            get_staking_contract_amounts_internal(staking_contract);
        staking_contract.principal = total_active_stake - commission_amount;

        // Short-circuit if there's no commission to pay.
        if (commission_amount == 0) {
            return 0
        };

        // Add a distribution for the operator.
        add_distribution(
            operator,
            staking_contract,
            operator,
            commission_amount
        );

        // Request to unlock the commission from the stake pool.
        // This won't become fully unlocked until the stake pool's lockup expires.
        stake::unlock_with_cap(commission_amount, &staking_contract.owner_cap);

        let pool_address = staking_contract.pool_address;
        emit(
            RequestCommission {
                operator,
                pool_address,
                accumulated_rewards,
                commission_amount
            }
        );

        commission_amount
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L678-696)
```text
    public entry fun unlock_stake(
        staker: &signer, operator: address, amount: u64
    ) acquires Store, BeneficiaryForOperator {
        // Short-circuit if amount is 0.
        if (amount == 0) return;

        let staker_address = signer::address_of(staker);
        assert_staking_contract_exists(staker_address, operator);

        let store = borrow_global_mut<Store>(staker_address);
        let staking_contract = store.staking_contracts.borrow_mut(&operator);

        // Force distribution of any already inactive stake.
        distribute_internal(
            staker_address,
            operator,
            staking_contract,
        );

```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L705-719)
```text
        // If there's less active stake remaining than the amount requested (potentially due to commission),
        // only withdraw up to the active amount.
        let (active, _, _, _) = stake::get_stake(staking_contract.pool_address);
        if (active < amount) {
            amount = active;
        };
        staking_contract.principal -= amount;

        // Record a distribution for the staker.
        add_distribution(
            operator,
            staking_contract,
            staker_address,
            amount,
        );
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L762-796)
```text
    public entry fun switch_operator(
        staker: &signer,
        old_operator: address,
        new_operator: address,
        new_commission_percentage: u64
    ) acquires Store, BeneficiaryForOperator {
        let staker_address = signer::address_of(staker);
        assert_staking_contract_exists(staker_address, old_operator);

        assert!(
            new_commission_percentage <= 100,
            error::invalid_argument(EINVALID_COMMISSION_PERCENTAGE)
        );
        // Merging two existing staking contracts is too complex as we'd need to merge two separate stake pools.
        let store = borrow_global_mut<Store>(staker_address);
        let staking_contracts = &mut store.staking_contracts;
        assert!(
            !staking_contracts.contains_key(&new_operator),
            error::invalid_state(ECANT_MERGE_STAKING_CONTRACTS)
        );

        let (_, staking_contract) = staking_contracts.remove(&old_operator);
        // Force distribution of any already inactive stake.
        distribute_internal(
            staker_address,
            old_operator,
            &mut staking_contract,
        );

        // For simplicity, we request commission to be paid out first. This avoids having to ensure to staker doesn't
        // withdraw into the commission portion.
        request_commission_internal(
            old_operator,
            &mut staking_contract,
        );
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L888-911)
```text
        // Buy all recipients out of the distribution pool.
        while (distribution_pool.shareholders_count() > 0) {
            let recipients = distribution_pool.shareholders();
            let recipient = recipients[0];
            let current_shares = distribution_pool.shares(recipient);
            let amount_to_distribute =
                distribution_pool.redeem_shares(recipient, current_shares);
            // If the recipient is the operator, send the commission to the beneficiary instead.
            if (recipient == operator) {
                recipient = beneficiary_for_operator(operator);
            };
            aptos_account::deposit_coins(
                recipient, coin::extract(&mut coins, amount_to_distribute)
            );

            emit(
                Distribute {
                    operator,
                    pool_address,
                    recipient,
                    amount: amount_to_distribute
                }
            );
        };
```
