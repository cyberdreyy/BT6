Based on my research, I found a plausible Aptos-native analog to the "non-standard token / reverting transfer causes stuck funds" bug class, located in `staking_contract.move`'s reliance on `aptos_account::deposit_coins`, which can be made to abort by an account owner opting out of unsolicited transfers.

### Title
Toxic operator/staker recipient can permanently brick `staking_contract` distribution, unlock, and operator-switch flows - ([File: aptos-move/framework/aptos-framework/sources/staking_contract.move])

### Summary
`distribute_internal` in `staking_contract.move` pays out every shareholder (staker and operator) in a single atomic loop using `aptos_account::deposit_coins`. [1](#0-0)  That helper aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the recipient is unregistered for the coin type and has opted out of unsolicited transfers via `set_allow_direct_coin_transfers(false)`. [2](#0-1) [3](#0-2)  Since `distribute_internal` is also invoked as a prerequisite step inside `unlock_stake`, `request_commission`, and `switch_operator`, a single "toxic" recipient in the pair's distribution pool can cause every one of these functions to abort permanently, functioning as the Move-native analog of a reverting/non-standard ERC20 transfer trapping funds in a contract.

### Finding Description
`distribute_internal` iterates all shareholders currently owed a payout from the `distribution_pool` (in practice just `staker` and `operator`), redeeming shares and calling `aptos_account::deposit_coins(recipient, ...)` for each one in a single transaction: [4](#0-3) 

`deposit_coins` will abort if the target address is not registered for the coin and has disabled arbitrary/direct coin transfers: [2](#0-1) 

Any account can flip this flag for itself, unprivileged, via `set_allow_direct_coin_transfers`: [3](#0-2) 

Because Move transactions are atomic, if any single recipient in the distribution pool aborts the deposit, the *entire* `distribute_internal` call reverts — no partial distribution occurs, and no other recipient (e.g. the innocent staker) gets paid either.

Crucially, `distribute_internal` is called as a forced step at the start of several other unprivileged entry points, not just `distribute`:
- `request_commission` calls it first. [5](#0-4) 
- `unlock_stake` calls it first. [6](#0-5) 
- `switch_operator` calls it first, before the staker can move to a new (non-toxic) operator. [7](#0-6) 

So if either the operator or the staker becomes "toxic" (address opts out of direct transfers while unregistered for `AptosCoin`) while they still hold unpaid shares in the `distribution_pool`, all four entry points (`distribute`, `request_commission`, `unlock_stake`, `switch_operator`) abort every time they are called, for as long as that toxic address remains a shareholder with a nonzero balance in the pool. Because `switch_operator` — the escape hatch for a staker to leave a bad operator — itself calls `distribute_internal`, the staker cannot even switch away from a toxic operator; both the staker's and operator's funds become stuck in the stake pool.

### Impact Explanation
This meets the "Stake And Lockup Gate" bar: it is a permanent, non-recoverable loss of claim rights over already-unlocked/inactive stake and reward/commission balances belonging to an account that did not cause the toxicity (e.g., a staker whose operator opts out of transfers, or an operator whose staker does the same). It blocks `unlock_stake`, `distribute`, `request_commission`, and `switch_operator` for the pair indefinitely, with no admin/governance override in `staking_contract.move` to force-remove a stuck shareholder from the `distribution_pool`. This is high impact because it can permanently strand real APT.

### Likelihood Explanation
Likelihood is moderate-to-low: it requires the toxic party (staker or operator) to explicitly call `set_allow_direct_coin_transfers(false)` while also never having registered a `CoinStore`/APT balance in the ordinary course (e.g. a fresh operator address created purely to receive commission), and the other party's actions being forced to route through `distribute_internal`. This is plausible for adversarial operators seeking to grief a staker or extort re-negotiation of commission terms, since the operator can trigger it unilaterally against a staker's stake, but it is a somewhat deliberate griefing action rather than an accidental one.

### Recommendation
Make payout resilient to a single failing/opted-out recipient, analogous to using `SafeERC20`-style non-reverting transfer patterns:
- Wrap each `aptos_account::deposit_coins` call in `distribute_internal` so a failure for one recipient does not abort the whole loop (e.g., detect `can_receive_direct_coin_transfers`/registration state up front and, if the recipient cannot receive, escrow that portion in a claimable holding structure instead of aborting).
- Alternatively, avoid depending on `distribute_internal` succeeding as a precondition inside `unlock_stake`/`request_commission`/`switch_operator`; decouple those state transitions from payout so a stuck payout cannot block unrelated stake-lockup operations.

### Proof of Concept
1. Operator account `O` calls `aptos_account::set_allow_direct_coin_transfers(false)` and never registers a `CoinStore<AptosCoin>` (e.g. a freshly-created operator address funded only through this staking_contract).
2. Staker `S` creates a staking contract with `O` as operator via `staking_contract::create_staking_contract*`.
3. Stake pool earns rewards; `O`'s commission and `S`'s rewards accumulate in the `distribution_pool`.
4. `S` calls `staking_contract::unlock_stake(S, O, amount)` → internally calls `distribute_internal`, which attempts `aptos_account::deposit_coins(O, commission_coins)`; because `O` is unregistered and opted out, this aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`, reverting the entire transaction. [6](#0-5) 
5. `S` attempts `switch_operator` to move away from `O` — also aborts for the same reason, since it also invokes `distribute_internal` first. [7](#0-6) 
6. `S` is now permanently unable to unlock, withdraw, or reassign the stake as long as `O` remains a shareholder with unpaid commission — funds are stuck.

**Note on verification limits:** I was unable to fully confirm within the available tool calls whether `coin::deposit`/`coin::is_account_registered` for `AptosCoin` specifically has been migrated to a fungible-asset path that would auto-create a store and bypass this abort (the `coin.move` source could not be located/read in the index). If AptosCoin deposits are now fully routed through `primary_fungible_store`/`ensure_primary_fungible_store_exists` (which auto-creates the store unconditionally, as seen in `aptos_account::deposit_fungible_assets`) rather than through `coin::is_account_registered`/`can_receive_direct_coin_transfers` gating, this abort path may not be reachable for `AptosCoin` in the current codebase, and the finding would not hold in practice. Because of this uncertainty around `coin.move` (not indexed), I recommend starting a Devin session with full repository access to verify the exact `AptosCoin`/`coin::deposit` migration state before treating this as confirmed.

### Citations

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L676-700)
```text
    /// Staker can call this to request withdrawal of part or all of their staking_contract.
    /// This also triggers paying commission to the operator for accounting simplicity.
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

        // For simplicity, we request commission to be paid out first. This avoids having to ensure to staker doesn't
        // withdraw into the commission portion.
        let commission_paid =
            request_commission_internal(
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L761-805)
```text
    /// Allows staker to switch operator without going through the lenghthy process to unstake.
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

        // Update the staking contract's commission rate and stake pool's operator.
        stake::set_operator_with_cap(&staking_contract.owner_cap, new_operator);
        staking_contract.commission_percentage = new_commission_percentage;

        let pool_address = staking_contract.pool_address;
        staking_contracts.add(new_operator, staking_contract);
        emit(SwitchOperator { pool_address, old_operator, new_operator });
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L855-920)
```text
    /// Distribute all unlocked (inactive) funds according to distribution shares.
    fun distribute_internal(
        staker: address,
        operator: address,
        staking_contract: &mut StakingContract,
    ) acquires BeneficiaryForOperator {
        let pool_address = staking_contract.pool_address;
        // Create the Staker resource if it doesn't exist to backfill the Staker resource for each pool.
        if (!exists<Staker>(pool_address)) {
            let pool_signer =
                &account::create_signer_with_capability(&staking_contract.signer_cap);
            move_to(pool_signer, Staker { staker });
        };
        let (_, inactive, _, pending_inactive) = stake::get_stake(pool_address);
        let total_potential_withdrawable = inactive + pending_inactive;
        let coins =
            stake::withdraw_with_cap(
                &staking_contract.owner_cap, total_potential_withdrawable
            );
        let distribution_amount = coin::value(&coins);
        if (distribution_amount == 0) {
            coin::destroy_zero(coins);
            return
        };

        let distribution_pool = &mut staking_contract.distribution_pool;
        update_distribution_pool(
            distribution_pool,
            distribution_amount,
            operator,
            staking_contract.commission_percentage
        );

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

        // In case there's any dust left, send them all to the staker.
        if (coin::value(&coins) > 0) {
            aptos_account::deposit_coins(staker, coins);
            distribution_pool.update_total_coins(0);
        } else {
            coin::destroy_zero(coins);
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L123-131)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L187-219)
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
            emit(
                DirectCoinTransferConfigUpdated {
                    account: addr,
                    new_allow_direct_transfers: allow
                }
            );
            move_to(account, direct_transfer_config);
        };
    }
```
