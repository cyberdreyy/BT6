Based on my investigation, the Aptos stake/delegation/vesting Move framework does not contain an analogous division-by-zero vulnerability.

The core share-accounting primitives (`pool_u64::amount_to_shares`, `pool_u64::shares_to_amount`, and their `pool_u64_unbound` equivalents) explicitly special-case the empty-pool condition — when `total_coins == 0` or `total_shares == 0`, the code branches to a multiplication by `scaling_factor` (or returns `0`) instead of performing a division, so the `distanceFromStrike == 0` style division-by-zero cannot occur here. [1](#0-0) [2](#0-1)  The unit tests explicitly verify these zero-total edge cases return safe values rather than aborting. [3](#0-2) 

In `staking_contract.move`, the commission math (`accumulated_rewards * commission_percentage / 100`) always divides by the constant `100`, never a user- or state-controlled value, so no zero-divisor path exists. <cite repo="EzraCole/aptos-core--005" path="aptos-move/framework/aptos-frame

### Citations

**File:** aptos-move/framework/aptos-stdlib/sources/pool_u64_unbound.spec.move (L87-94)
```text
    spec fun spec_amount_to_shares_with_total_coins(pool: Pool, coins_amount: u64, total_coins: u64): u128 {
        if (pool.total_coins == 0 || pool.total_shares == 0) {
            coins_amount * pool.scaling_factor
        }
        else {
            (coins_amount * pool.total_shares) / total_coins
        }
    }
```

**File:** aptos-move/framework/aptos-stdlib/sources/pool_u64.spec.move (L277-305)
```text
    spec amount_to_shares(self: &Pool, coins_amount: u64): u64 {
        pragma opaque = true;
        aborts_if self.total_coins > 0 && self.total_shares > 0
            && (coins_amount * self.total_shares) / self.total_coins > MAX_U64;
        aborts_if (self.total_coins == 0 || self.total_shares == 0)
            && coins_amount * self.scaling_factor > MAX_U64;
        // self.total_coins > 0 && self.total_coins == 0 is always false — no abort needed here.
        ensures result == spec_amount_to_shares_with_total_coins(self, coins_amount, self.total_coins);
    }

    spec create_with_scaling_factor(shareholders_limit: u64, scaling_factor: u64): Pool {
        pragma opaque = true;
        ensures result == Pool {
            shareholders_limit: shareholders_limit,
            total_coins: 0,
            total_shares: 0,
            shares: simple_map::spec_new<address, u64>(),
            shareholders: vector[],
            scaling_factor: scaling_factor
        };
        aborts_if false;
    }

    spec shares_to_amount(self: &Pool, shares: u64): u64 {
        pragma opaque = true;
        aborts_if self.total_coins > 0 && self.total_shares > 0
            && (shares * self.total_coins) / self.total_shares > MAX_U64;
        ensures result == spec_shares_to_amount_with_total_coins(self, shares, self.total_coins);
    }
```

**File:** aptos-move/framework/aptos-stdlib/sources/pool_u64.move (L535-566)
```text
    public entry fun test_amount_to_shares_empty_pool() {
        let pool = new(1);
        // No total shares and total coins.
        assert!(pool.amount_to_shares(1000) == 1000, 0);

        // No total shares but some total coins.
        pool.update_total_coins(1000);
        assert!(pool.amount_to_shares(1000) == 1000, 1);

        // No total coins but some total shares.
        pool.update_total_coins(0);
        pool.add_shares(@1, 100);
        assert!(pool.amount_to_shares(1000) == 1000, 2);
        pool.destroy_pool();
    }

    #[test]
    public entry fun test_shares_to_amount_empty_pool() {
        let pool = new(1);
        // No total shares and total coins.
        assert!(pool.shares_to_amount(1000) == 0, 0);

        // No total shares but some total coins.
        pool.update_total_coins(1000);
        assert!(pool.shares_to_amount(1000) == 0, 1);

        // No total coins but some total shares.
        pool.update_total_coins(0);
        pool.add_shares(@1, 100);
        assert!(pool.shares_to_amount(1000) == 0, 2);
        pool.destroy_pool();
    }
```
