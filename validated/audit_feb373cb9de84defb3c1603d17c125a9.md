[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/datastructures/ordered_map.spec.move (L92-97)
```text
    // Over-approximates the template's cmp-order-violation abort path (modeled
    // nondeterministically): when `old_key != new_key`, returns true even though
    // the actual call may succeed if the order precondition holds.
    spec fun spec_aborts_replace_key_inplace<K, V>(m: OrderedMap<K, V>, old_key: K, new_key: K): bool {
        !spec_contains_key(m, old_key) || old_key != new_key
    }
```

**File:** aptos-move/framework/aptos-framework/sources/datastructures/ordered_map.spec.move (L217-219)
```text
    spec replace_key_inplace {
        pragma intrinsic;
    }
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1-1)
```text
///
```
