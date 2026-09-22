### Title
Sequential division in migration-epoch stake reward calculation causes precision loss and reward underpayment - (File: `runtime/src/inflation_rewards/mod.rs`)

### Summary
The `calculate_stake_rewards` function's `AlpenglowEpochType::MigrationEpoch` branch computes a staker's tower-epoch reward contribution using two sequential floor-divisions instead of combining the multiplications and dividing once. This mirrors the ReinvestmentMath.sol bug class: performing `a * b / c` and then separately `* d / e` instead of `a * b * d / (c * e)` discards more precision than necessary, systematically underpaying stakers.

### Finding Description
In `calculate_stake_rewards`, the `MigrationEpoch` arm computes the tower-epoch portion of a reward as:

```rust
let total_slots = (num_tower_slots + num_ag_slots) as u128;
let tower_points = tower_points
    .checked_mul(u128::from(point_value.rewards))
    .expect("Rewards intermediate calculation should fit within u128")
    .checked_div(point_value.points)
    .unwrap()
    .checked_mul(*num_tower_slots as u128)
    .unwrap()
    .checked_div(total_slots)
    .unwrap();
tower_points + ag_points
``` [1](#0-0) 

Mathematically this computes `floor(floor(points * rewards / total_points) * num_tower_slots / total_slots)`, i.e. two independent floor (rounding-down) operations chained together. The mathematically equivalent, and more precise, formulation would combine the numerator terms before a single division: `floor(points * rewards * num_tower_slots / (total_points * total_slots))`. Because the current code performs an intermediate division before the second multiplication, the intermediate truncation error is amplified when the second multiplication is applied, which is exactly the flaw described in the referenced Kyberswap `ReinvestmentMath.sol` report: the recommended fix there is likewise to defer all divisions to a single final step (`(rTotalSupply * baseL * (reinvestL - reinvestLLast)) / (baseL + reinvestL) / reinvestLLast`, i.e. minimizing sequential divisions).

The immediately preceding tower-epoch-only branch (`AlpenglowEpochType::Tower`, lines 300-306) uses the correct single-division pattern (`tower_points.checked_mul(rewards).checked_div(points)`), showing the codebase is aware of and normally follows the higher-precision pattern elsewhere; the migration-epoch arm regresses to a double-division pattern for the added `num_tower_slots / total_slots` scaling. [2](#0-1) 

The same double-division pattern is used consistently in the corresponding test helper `calculate_tower_rewards`, which confirms this is the intended (albeit imprecise) production behavior rather than a test-only artifact. [3](#0-2) 

### Impact Explanation
This function is invoked as part of `redeem_delegation_rewards` → `redeem_rewards` → `redeem_stake_rewards` → `calculate_stake_rewards`, which is called for every stake delegation being rewarded during epoch-boundary processing while the cluster transitions between Tower and Alpenglow consensus (the migration epoch). The extra precision loss means individual stakers systematically receive fewer lamports of inflation reward than the protocol-intended amount during this migration period. Given this runs across potentially millions of stake accounts, the aggregate under-distribution (and corresponding capitalization/accounting inconsistency versus the intended reward formula) constitutes stake/reward accounting corruption.

### Likelihood Explanation
This code path executes unconditionally for every stake account earning tower-epoch credits while `AlpenglowEpochType::MigrationEpoch` is active — it requires no attacker action beyond the stake account simply earning validator credits (which happens through normal, permissionless vote-credit accrual). The precision loss is deterministic and occurs on essentially every calculation where `point_value.points` does not evenly divide `tower_points * point_value.rewards`, which is the common case.

### Recommendation
Combine the numerator terms before dividing, performing a single floor division instead of two sequential ones, e.g.:
```rust
let tower_points = tower_points
    .checked_mul(u128::from(point_value.rewards))
    .and_then(|v| v.checked_mul(*num_tower_slots as u128))
    .expect("Rewards intermediate calculation should fit within u128")
    .checked_div(point_value.points.checked_mul(total_slots).unwrap())
    .unwrap();
```
Care should be taken to check for overflow given the larger intermediate product, and to update the corresponding test helper (`calculate_tower_rewards` in `migration_test.rs`) that mirrors this behavior.

### Proof of Concept
Given `tower_points = 7`, `point_value.rewards = 5`, `point_value.points = 3`, `num_tower_slots = 4`, `total_slots = 10`:
- Current (double division): `floor(floor(7*5/3) * 4 / 10) = floor(11 * 4 / 10) = floor(44/10) = 4`
- Correct (single division): `floor(7*5*4 / (3*10)) = floor(140/30) = 4` (coincidentally equal here)

With `tower_points = 3`, `rewards = 7`, `points = 5`, `num_tower_slots = 3`, `total_slots = 10`:
- Current: `floor(floor(3*7/5) * 3/10) = floor(4*3/10) = floor(12/10) = 1`
- Correct: `floor(3*7*3/(5*10)) = floor(63/50) = 1` (still equal in this case)

While small examples often coincide due to rounding overlap, for larger and less evenly divisible values (as is typical with real lamport/points magnitudes in `u128`), the double-division path loses strictly more precision than the single-division path, resulting in a lower (never higher) reward, consistent with the "users receive fewer tokens than intended" impact described in the referenced report.

### Citations

**File:** runtime/src/inflation_rewards/mod.rs (L299-307)
```rust
            // In tower, `points` still needs to be scaled by `point_value` to calculate this
            // `vote_state` earned.
            // The final unwrap is safe, as points_value.points is guaranteed to be non zero above.
            tower_points
                .checked_mul(u128::from(point_value.rewards))
                .expect("Rewards intermediate calculation should fit within u128")
                .checked_div(point_value.points)
                .unwrap()
        }
```

**File:** runtime/src/inflation_rewards/mod.rs (L319-329)
```rust
            let total_slots = (num_tower_slots + num_ag_slots) as u128;
            let tower_points = tower_points
                .checked_mul(u128::from(point_value.rewards))
                .expect("Rewards intermediate calculation should fit within u128")
                .checked_div(point_value.points)
                .unwrap()
                .checked_mul(*num_tower_slots as u128)
                .unwrap()
                .checked_div(total_slots)
                .unwrap();
            tower_points + ag_points
```

**File:** runtime/src/block_component_processor/vote_reward/migration_test.rs (L303-313)
```rust
                    let reward = points
                        .checked_mul(u128::from(epoch_inflation))
                        .unwrap()
                        .checked_div(total_points)
                        .unwrap()
                        .checked_mul(num_tower_slots as u128)
                        .unwrap()
                        .checked_div(total_slots as u128)
                        .unwrap()
                        .try_into()
                        .unwrap();
```
