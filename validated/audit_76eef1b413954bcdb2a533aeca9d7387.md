No vulnerability found for this question.

**Analysis:**

The claimed race does not exist because of two independent structural protections:

1. **Hard cap at extraction time.** `stake::unlock_with_cap` caps the amount to the *current* active balance immediately before extracting, in the same atomic step:
```
let amount = min(amount, coin::value(&stake_pool.active));
let unlocked_stake = coin::extract(&mut stake_pool.active, amount);
coin::merge<AptosCoin>(&mut stake_pool.pending_inactive, unlocked_stake);
``` [1](#0-0) 
There is no window where `pending_inactive` can receive more than what was actually held in `active` — `coin::extract` operates on the live `Coin<AptosCoin>` resource, and `min()` guarantees the transferred amount never exceeds the balance read microseconds earlier in the same, non-preemptible function execution. This behavior is explicitly covered by `test_active_validator_unlocking_more_than_available_stake_should_cap`, which unlocks 200 against only 100 active and asserts the result caps at 100. [2](#0-1) 

2. **No cross-transaction interleaving inside a single call.** `delegation_pool::unlock_internal` reads `active` via `stake::get_stake`, asserts `amount <= active`, redeems shares, then calls `stake::unlock`, all within one uninterrupted Move function invocation:
```
let (active, _, _, _) = stake::get_stake(pool_address);
assert!(amount <= active, error::invalid_argument(ENOT_ENOUGH_ACTIVE_STAKE_TO_UNLOCK));
...
amount = redeem_active_shares(pool, delegator_address, amount);
stake::unlock(&retrieve_stake_pool_owner(pool), amount);
``` [3](#0-2) 
Move/Aptos transaction execution is atomic per-transaction — there is no mechanism (including under Block-STM parallel execution, which enforces serializable output via validation/re-execution) by which an `add_stake` transaction can be interleaved *inside* the execution of this function to change `stake_pool.active` between the read and the `stake::unlock` call. The "concurrently interleaves add_stake and unlock calls" premise in the proof idea describes a execution model that does not exist in Aptos; transactions apply their effects atomically and sequentially from the state machine's perspective, and Block-STM's optimistic concurrency control re-executes any transaction whose read-set was invalidated by a conflicting write, which would force a proper re-check of `active` rather than allowing a stale value to be used.

Because the extraction is unconditionally capped by the actual coin balance and no legal execution interleaving can inject a stale `active` value into `stake::unlock_with_cap`, the described accounting corruption (active + pending_inactive exceeding the pre-operation sum) cannot occur.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1160-1163)
```text
        // Cap amount to unlock by maximum active stake.
        let amount = min(amount, coin::value(&stake_pool.active));
        let unlocked_stake = coin::extract(&mut stake_pool.active, amount);
        coin::merge<AptosCoin>(&mut stake_pool.pending_inactive, unlocked_stake);
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L2817-2828)
```text
    #[test(aptos_framework = @aptos_framework, validator = @0x123)]
    public entry fun test_active_validator_unlocking_more_than_available_stake_should_cap(
        aptos_framework: &signer, validator: &signer
    ) acquires AllowedValidators, AptosCoinCapabilities, OwnerCapability, PendingTransactionFee, PrecomputedValidatorSet, StakePool, TransactionFeeConfig, ValidatorConfig, ValidatorPerformance, ValidatorSet {
        initialize_for_test(aptos_framework);
        let (_sk, pk, pop) = generate_identity();
        initialize_test_validator(&pk, &pop, validator, 100, false, false);

        // Validator unlocks more stake than they have active. This should limit the unlock to 100.
        unlock(validator, 200);
        assert_validator_state(signer::address_of(validator), 0, 0, 0, 100, 0);
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1547-1560)
```text
        // fail unlock of more stake than `active` on the stake pool
        let (active, _, _, _) = stake::get_stake(pool_address);
        assert!(amount <= active, error::invalid_argument(ENOT_ENOUGH_ACTIVE_STAKE_TO_UNLOCK));

        let pool = borrow_global_mut<DelegationPool>(pool_address);
        amount = coins_to_transfer_to_ensure_min_stake(
            &pool.active_shares,
            pending_inactive_shares_pool(pool),
            delegator_address,
            amount,
        );
        amount = redeem_active_shares(pool, delegator_address, amount);

        stake::unlock(&retrieve_stake_pool_owner(pool), amount);
```
