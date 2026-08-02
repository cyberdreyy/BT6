[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L26-29)
```text
/// Admin flow:
/// 1. After creating the vesting contract, admin cannot change the vesting schedule.
/// 2. Admin can call update_voter, update_operator, or reset_lockup at any time to update the underlying staking
/// contract.
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L108-123)
```text
    struct VestingSchedule has copy, drop, store {
        // The vesting schedule as a list of fractions that vest for each period. The last number is repeated until the
        // vesting amount runs out.
        // For example [1/24, 1/24, 1/48] with a period of 1 month means that after vesting starts, the first two months
        // will vest 1/24 of the original total amount. From the third month only, 1/48 will vest until the vesting fund
        // runs out.
        // u32/u32 should be sufficient to support vesting schedule fractions.
        schedule: vector<FixedPoint32>,
        // When the vesting should start.
        start_timestamp_secs: u64,
        // In seconds. How long each vesting period is. For example 1 month.
        period_duration: u64,
        // Last vesting period, 1-indexed. For example if 2 months have passed, the last vesting period, if distribution
        // was requested, would be 2. Default value is 0 which means there have been no vesting periods yet.
        last_vested_period: u64,
    }
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L136-161)
```text
    struct VestingContract has key {
        state: u64,
        admin: address,
        grant_pool: Pool,
        beneficiaries: SimpleMap<address, address>,
        vesting_schedule: VestingSchedule,
        // Withdrawal address where all funds would be released back to if the admin ends the vesting for a specific
        // account or terminates the entire vesting contract.
        withdrawal_address: address,
        staking: StakingInfo,
        // Remaining amount in the grant. For calculating accumulated rewards.
        remaining_grant: u64,
        // Used to control staking.
        signer_cap: SignerCapability,

        // Events.
        update_operator_events: EventHandle<UpdateOperatorEvent>,
        update_voter_events: EventHandle<UpdateVoterEvent>,
        reset_lockup_events: EventHandle<ResetLockupEvent>,
        set_beneficiary_events: EventHandle<SetBeneficiaryEvent>,
        unlock_rewards_events: EventHandle<UnlockRewardsEvent>,
        vest_events: EventHandle<VestEvent>,
        distribute_events: EventHandle<DistributeEvent>,
        terminate_events: EventHandle<TerminateEvent>,
        admin_withdraw_events: EventHandle<AdminWithdrawEvent>,
    }
```
