[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/datastructures/ordered_map.move (L684-690)
```text
    // return index containing the key, or insert position.
    // I.e. index of first element that has key larger or equal to the passed `key` argument.
    fun binary_search<K, V>(key: &K, entries: &vector<Entry<K, V>>, start: u64, end: u64): u64 {
        let l = start;
        let r = end;
        while (l != r) {
            let mid = l + ((r - l) >> 1);
```

**File:** aptos-move/framework/aptos-framework/sources/datastructures/ordered_map.move (L722-730)
```text
    #[test_only]
    public fun validate_ordered<K, V>(self: &OrderedMap<K, V>) {
        let len = self.entries.length();
        let i = 1;
        while (i < len) {
            assert!(cmp::compare(&self.entries.borrow(i).key, &self.entries.borrow(i - 1).key).is_gt(), 1);
            i += 1;
        };
    }
```
