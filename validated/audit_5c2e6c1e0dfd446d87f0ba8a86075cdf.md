No vulnerability found for this question.

**Reasoning:**

The premise of the question misunderstands the authorization model of `set_beneficiary_for_operator`. The function only checks the feature flag as a gate on whether the beneficiary-change mechanism is available at all — it does not use the flag for any access-control decision tied to *which* address's beneficiary gets modified: [1](#0-0) 

The beneficiary record is always written to (or read from) `operator_addr = signer::address_of(operator)` — i.e., the resource is `move_to`'d under the signer's own address, and only that signer can mutate their own `BeneficiaryForOperator` resource. There is no staking-pool lookup, no separate "operator" parameter that could be spoofed, and no cross-account write path: an attacker without the true operator's private key cannot produce a signer for that address, regardless of how the `OPERATOR_BENEFICIARY_CHANGE` feature flag (id 39) is toggled [2](#0-1) .

Additionally, feature flag changes are not applied mid-epoch/mid-transaction for user-invoked entry functions. They're staged via `change_feature_flags_for_next_epoch` and only take effect at `on_new_epoch`, which requires governance/framework signer privileges to invoke in the first place [3](#0-2) . Toggling this flag is itself a privileged governance action, which is explicitly out of scope per the review bounds ("Reject anything that assumes the attacker already owns... governance authority").

Even granting the flag toggling for the sake of argument, the existing test `test_operator_can_set_beneficiary` demonstrates that only the calling operator's own signer can set/overwrite their `BeneficiaryForOperator` entry, and a switch to a different operator does not inherit or corrupt the prior operator's beneficiary mapping [4](#0-3) . There is no code path by which an unprivileged attacker (not holding the operator's signing key) can set or corrupt another operator's beneficiary address, so the described invariant break does not exist.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L811-829)
```text
        operator: &signer, new_beneficiary: address
    ) acquires BeneficiaryForOperator {
        assert!(
            features::operator_beneficiary_change_enabled(),
            std::error::invalid_state(EOPERATOR_BENEFICIARY_CHANGE_NOT_SUPPORTED)
        );
        // The beneficiay address of an operator is stored under the operator's address.
        // So, the operator does not need to be validated with respect to a staking pool.
        let operator_addr = signer::address_of(operator);
        let old_beneficiary = beneficiary_for_operator(operator_addr);
        if (exists<BeneficiaryForOperator>(operator_addr)) {
            borrow_global_mut<BeneficiaryForOperator>(operator_addr).beneficiary_for_operator =
                new_beneficiary;
        } else {
            move_to(
                operator,
                BeneficiaryForOperator { beneficiary_for_operator: new_beneficiary }
            );
        };
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1741-1799)
```text
        // Set beneficiary.
        set_beneficiary_for_operator(operator1, beneficiary_address);
        assert!(beneficiary_for_operator(operator1_address) == beneficiary_address, 0);

        // Fast forward to generate rewards.
        stake::end_epoch();
        let new_balance = with_rewards(INITIAL_BALANCE);
        stake::assert_stake_pool(pool_address, new_balance, 0, 0, 0);

        // Operator claims 10% of rewards so far as commissions.
        let expected_commission_1 =
            (new_balance - last_recorded_principal(staker_address, operator1_address))
                / 10;
        new_balance -= expected_commission_1;
        request_commission(operator1, staker_address, operator1_address);
        stake::assert_stake_pool(
            pool_address,
            new_balance,
            0,
            0,
            expected_commission_1
        );
        assert!(
            last_recorded_principal(staker_address, operator1_address) == new_balance, 0
        );
        assert_distribution(
            staker_address,
            operator1_address,
            operator1_address,
            expected_commission_1
        );
        stake::fast_forward_to_unlock(pool_address);

        // Both original stake and operator commissions have received rewards.
        expected_commission_1 = with_rewards(expected_commission_1);
        new_balance = with_rewards(new_balance);
        stake::assert_stake_pool(
            pool_address,
            new_balance,
            expected_commission_1,
            0,
            0
        );
        distribute(staker_address, operator1_address);
        let operator_balance = coin::balance<AptosCoin>(operator1_address);
        let beneficiary_balance = coin::balance<AptosCoin>(beneficiary_address);
        let expected_operator_balance = INITIAL_BALANCE;
        let expected_beneficiary_balance = expected_commission_1;
        assert!(operator_balance == expected_operator_balance, operator_balance);
        assert!(beneficiary_balance == expected_beneficiary_balance, beneficiary_balance);
        stake::assert_stake_pool(pool_address, new_balance, 0, 0, 0);
        assert_no_pending_distributions(staker_address, operator1_address);

        // switch operator to operator2. The rewards should go to operator2 not to the beneficiay of operator1.
        let old_beneficiay_balance = beneficiary_balance;
        switch_operator(
            staker,
            operator1_address,
            operator2_address,
```

**File:** aptos-move/framework/move-stdlib/sources/configs/features.move (L353-363)
```text
    /// Whether allow changing beneficiaries for operators.
    /// Lifetime: transient
    const OPERATOR_BENEFICIARY_CHANGE: u64 = 39;

    public fun get_operator_beneficiary_change_feature(): u64 {
        OPERATOR_BENEFICIARY_CHANGE
    }

    public fun operator_beneficiary_change_enabled(): bool {
        is_enabled(OPERATOR_BENEFICIARY_CHANGE)
    }
```

**File:** aptos-move/framework/move-stdlib/doc/features.md (L2112-2130)
```markdown
## Function `change_feature_flags_for_next_epoch` <a id="0x1_features_change_feature_flags_for_next_epoch"></a>

Enable and disable features for the next epoch.


<pre><code><b>public</b> <b>fun</b> <a href="features.md#0x1_features_change_feature_flags_for_next_epoch">change_feature_flags_for_next_epoch</a>(framework: &<a href="signer.md#0x1_signer">signer</a>, enable: <a href="vector.md#0x1_vector">vector</a>&lt;u64&gt;, disable: <a href="vector.md#0x1_vector">vector</a>&lt;u64&gt;)
</code></pre>


## Function `on_new_epoch` <a id="0x1_features_on_new_epoch"></a>

Apply all the pending feature flag changes. Should only be used at the end of a reconfiguration with DKG.

While the scope is public, it can only be usd in system transactions like <code>block_prologue</code> and governance proposals,
who have permission to set the flag that's checked in <code>extract()</code>.


<pre><code><b>public</b> <b>fun</b> <a href="features.md#0x1_features_on_new_epoch">on_new_epoch</a>(framework: &<a href="signer.md#0x1_signer">signer</a>)
</code></pre>
```
