No vulnerability found for this question.

**Reasoning:**

`update_stake_pool` is a private (non-public, non-entry) function in `stake.move`, callable only internally from `stake::on_new_epoch`, which is itself `public(friend)` and can only be invoked by friend modules `reconfiguration` and `reconfiguration_with_dkg`. [1](#0-0) 

`on_new_epoch` is called exactly once per successful reconfiguration inside `reconfiguration::reconfigure()`, which itself is `public(friend)` and guarded against duplicate execution within the same transaction/time (`if (current_time == config_ref.last_reconfiguration_time) { return }`). [2](#0-1) 

`reconfiguration_state::on_reconfig_start` and `on_reconfig_finish` are `public(friend)` functions restricted to `reconfiguration`, `reconfiguration_with_dkg`, and `stake` — no unprivileged entrypoint can call these directly or repeatedly. [3](#0-2) [4](#0-3) 

The DKG-based async path (`reconfiguration_with_dkg::try_start`/`finish`) also does not create repeated `update_stake_pool` invocations: `try_start` is a no-op if a reconfig is already in progress for the current epoch, and `finish` requires the `@aptos_framework` signer (`system_addresses::assert_aptos_framework`), which is not obtainable by an unprivileged actor. [5](#0-4) [6](#0-5) 

Since `is_in_progress()` only affects `get_reconfig_start_time_secs()` used to determine whether the current lockup has expired (i.e., whether `pending_inactive` moves to `inactive`), and there is no unprivileged path to invoke `update_stake_pool` (or even `on_reconfig_start`) more than once per actual epoch transition, the premise of the question — that an attacker can trigger `update_stake_pool` multiple times within one `is_in_progress()==true` window via an unprivileged call — does not hold. This finding assumes control over privileged/system-only reconfiguration flows, which is out of scope per the review's decision standard rejecting cases where the attacker doesn't already have the required privileged path. [7](#0-6)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1343-1360)
```text
    public(friend) fun on_new_epoch() acquires AptosCoinCapabilities, PendingTransactionFee, PrecomputedValidatorSet, StakePool, TransactionFeeConfig, ValidatorConfig, ValidatorPerformance, ValidatorSet {
        let validator_set = borrow_global_mut<ValidatorSet>(@aptos_framework);
        let config = staking_config::get();
        let validator_perf = borrow_global_mut<ValidatorPerformance>(@aptos_framework);

        // Process pending stake and distribute transaction fees and rewards for each currently active validator.
        validator_set.active_validators.for_each_ref(|validator| {
            let validator: &ValidatorInfo = validator;
            update_stake_pool(validator_perf, validator.addr, &config);
        });

        // Process pending stake and distribute transaction fees and rewards for each currently pending_inactive validator
        // (requested to leave but not removed yet).
        validator_set.pending_inactive.for_each_ref(|validator| {
            let validator: &ValidatorInfo = validator;
            update_stake_pool(validator_perf, validator.addr, &config);
        });

```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1964-1971)
```text
    /// Assuming we are in a middle of a reconfiguration (no matter it is immediate or async), get its start time.
    fun get_reconfig_start_time_secs(): u64 {
        if (reconfiguration_state::is_in_progress()) {
            reconfiguration_state::start_time_secs()
        } else {
            timestamp::now_seconds()
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/reconfiguration.move (L109-137)
```text
    /// Signal validators to start using new configuration. Must be called from friend config modules.
    public(friend) fun reconfigure() acquires Configuration {
        // Do not do anything if genesis has not finished.
        if (chain_status::is_genesis()
            || timestamp::now_microseconds() == 0
            || !reconfiguration_enabled()) { return };

        let config_ref = borrow_global_mut<Configuration>(@aptos_framework);
        let current_time = timestamp::now_microseconds();

        // Do not do anything if a reconfiguration event is already emitted within this transaction.
        //
        // This is OK because:
        // - The time changes in every non-empty block
        // - A block automatically ends after a transaction that emits a reconfiguration event, which is guaranteed by
        //   VM spec that all transactions comming after a reconfiguration transaction will be returned as Retry
        //   status.
        // - Each transaction must emit at most one reconfiguration event
        //
        // Thus, this check ensures that a transaction that does multiple "reconfiguration required" actions emits only
        // one reconfiguration event.
        //
        if (current_time == config_ref.last_reconfiguration_time) { return };

        reconfiguration_state::on_reconfig_start();

        // Call stake to compute the new validator set and distribute rewards and transaction fees.
        stake::on_new_epoch();
        storage_gas::on_reconfig();
```

**File:** aptos-move/framework/aptos-framework/sources/reconfiguration_state.move (L11-13)
```text
    friend aptos_framework::reconfiguration;
    friend aptos_framework::reconfiguration_with_dkg;
    friend aptos_framework::stake;
```

**File:** aptos-move/framework/aptos-framework/sources/reconfiguration_state.move (L69-106)
```text
    public(friend) fun on_reconfig_start() acquires State {
        if (exists<State>(@aptos_framework)) {
            let state = borrow_global_mut<State>(@aptos_framework);
            let variant_type_name = *state.variant.type_name().bytes();
            if (variant_type_name == b"0x1::reconfiguration_state::StateInactive") {
                state.variant = copyable_any::pack(
                    StateActive { start_time_secs: timestamp::now_seconds() }
                );
            }
        };
    }

    /// Get the unix time when the currently in-progress reconfiguration started.
    /// Abort if the reconfiguration state is not "in progress".
    public(friend) fun start_time_secs(): u64 acquires State {
        let state = borrow_global<State>(@aptos_framework);
        let variant_type_name = *state.variant.type_name().bytes();
        if (variant_type_name == b"0x1::reconfiguration_state::StateActive") {
            let active = state.variant.unpack<StateActive>();
            active.start_time_secs
        } else {
            abort(error::invalid_state(ERECONFIG_NOT_IN_PROGRESS))
        }
    }

    /// Called at the end of every reconfiguration to mark the state as "stopped".
    /// Abort if the current state is not "in progress".
    public(friend) fun on_reconfig_finish() acquires State {
        if (exists<State>(@aptos_framework)) {
            let state = borrow_global_mut<State>(@aptos_framework);
            let variant_type_name = *state.variant.type_name().bytes();
            if (variant_type_name == b"0x1::reconfiguration_state::StateActive") {
                state.variant = copyable_any::pack(StateInactive {});
            } else {
                abort(error::invalid_state(ERECONFIG_NOT_IN_PROGRESS))
            }
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/reconfiguration_with_dkg.move (L42-66)
```text
    public(friend) fun try_start() {
        let incomplete_dkg_session = dkg::incomplete_session();
        if (incomplete_dkg_session.is_some()) {
            let session = incomplete_dkg_session.borrow();
            if (dkg::session_dealer_epoch(session) == reconfiguration::current_epoch()) {
                return
            }
        };
        // V1 prologue dispatch means chunky DKG is not running this attempt;
        // drop any stale chunky session so finish_with_dkg_result can proceed
        // (e.g., recovery from a stall via local chunky_dkg_override_seq_num).
        if (chunky_dkg::incomplete_session().is_some()) {
            let framework = create_signer::create_signer(@aptos_framework);
            chunky_dkg::try_clear_incomplete_session(&framework);
        };

        reconfiguration_state::on_reconfig_start();
        let cur_epoch = reconfiguration::current_epoch();
        dkg::start(
            cur_epoch,
            randomness_config::current(),
            stake::cur_validator_consensus_infos(),
            validator_consensus_infos_from_validator_set(&stake::next_validator_consensus_infos_v2())
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/reconfiguration_with_dkg.move (L96-116)
```text
    public(friend) fun finish(framework: &signer) {
        system_addresses::assert_aptos_framework(framework);
        dkg::try_clear_incomplete_session(framework);
        chunky_dkg::try_clear_incomplete_session(framework);
        consensus_config::on_new_epoch(framework);
        execution_config::on_new_epoch(framework);
        gas_schedule::on_new_epoch(framework);
        std::version::on_new_epoch(framework);
        features::on_new_epoch(framework);
        jwk_consensus_config::on_new_epoch(framework);
        jwks::on_new_epoch(framework);
        keyless_account::on_new_epoch(framework);
        randomness_config_seqnum::on_new_epoch(framework);
        randomness_config::on_new_epoch(framework);
        randomness_api_v0_config::on_new_epoch(framework);
        chunky_dkg_config_seqnum::on_new_epoch(framework);
        chunky_dkg_config::on_new_epoch(framework);
        epoch_timeout_config::on_new_epoch(framework);
        decryption::on_new_epoch(framework, reconfiguration::current_epoch() + 1);
        reconfiguration::reconfigure();
    }
```
