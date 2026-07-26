I have found a clear analog. Let me confirm the exact mechanics before writing the report.### Title
Last-Added Oracle Overwrites `max_timestamp`, Anchoring Freshness Window to a Stale Feed — (`File: crates/sui-oracle/move/oracle/sources/meta_oracle.move`)

---

### Summary

`add_simple_oracle` unconditionally overwrites `MetaOracle.max_timestamp` with the timestamp of every oracle added, regardless of whether that timestamp is newer or older than the current value. Because `combine` derives its freshness cutoff as `max_timestamp - time_window_ms`, an attacker who controls the insertion order can anchor the entire window to a stale oracle's timestamp, causing all other stale feeds to pass the freshness check and be included in the median price calculation.

---

### Finding Description

`MetaOracle<T>` tracks a single `max_timestamp` field that is meant to represent the most-recent data point across all added oracles. The freshness filter in `combine` computes:

```
let min_timestamp = max_timestamp - time_window_ms;
```

and admits every oracle whose `data::timestamp > min_timestamp`.

The bug is in `add_simple_oracle`:

```move
public fun add_simple_oracle<T: copy + drop + store>(
    meta_oracle: &mut MetaOracle<T>,
    oracle: &SimpleOracle
) {
    let oracle_data = oracle::simple_oracle::get_latest_data(oracle, meta_oracle.ticker);
    if (option::is_some(&oracle_data)) {
        meta_oracle.max_timestamp = data::timestamp(option::borrow(&oracle_data));  // ← unconditional overwrite
    };
    vector::push_back(&mut meta_oracle.oracle_data, oracle_data);
}
```

`max_timestamp` is **overwritten** on every call, not updated to the maximum. After all oracles are added, `max_timestamp` equals the timestamp of the **last oracle added**, not the freshest one.

`MetaOracle<T>` has no `key` or `store` ability — it is constructed locally inside a transaction. Any caller therefore controls the insertion order freely.

**Concrete scenario (time_window_ms = 60 000 ms):**

| Oracle | Timestamp | Age |
|--------|-----------|-----|
| A | T (now) | fresh |
| B | T − 3 600 000 | 1 hour stale |
| C | T − 7 200 000 | 2 hours stale |

Caller adds A, B, C in that order → `max_timestamp = T − 7 200 000`.
`min_timestamp = T − 7 200 000 − 60 000 = T − 7 260 000`.

All three oracles satisfy `timestamp > T − 7 260 000`, so all three pass. The median of three hour-old prices is returned as "trusted" data.

If the caller adds C, B, A instead, `max_timestamp = T`, `min_timestamp = T − 60 000`, and only A passes — the correct behavior.

---

### Impact Explanation

Any contract that calls `meta_oracle::median` (or `combine` indirectly) for financial decisions — such as the `trusted_fx` pattern that converts MIST to USD — will receive a median price derived from arbitrarily stale feeds. This constitutes **harmful smart-contract behavior**: the staleness guard that is the entire purpose of `MetaOracle` is silently bypassed, and downstream contracts act on incorrect prices. This maps to the High/Medium bounty class of "harmful smart-contract behavior."

---

### Likelihood Explanation

`MetaOracle<T>` is a hot-potato-style value constructed entirely within the caller's transaction. No privileged role is required. Any ordinary SUI holder who calls a contract that exposes `add_simple_oracle` in a user-controlled order, or who deploys their own contract using the oracle package, can trigger this. The only prerequisite is that at least one `SimpleOracle` shared object contains a stale price entry, which is a normal operational condition (feeds can lag).

---

### Recommendation

Replace the unconditional assignment in `add_simple_oracle` with a maximum-tracking update:

```move
if (option::is_some(&oracle_data)) {
    let ts = data::timestamp(option::borrow(&oracle_data));
    if (ts > meta_oracle.max_timestamp) {
        meta_oracle.max_timestamp = ts;
    };
};
```

This ensures `max_timestamp` always reflects the freshest data point seen, so `min_timestamp` is anchored to the most recent feed and stale oracles are correctly excluded.

---

### Proof of Concept

```move
// Attacker constructs MetaOracle with threshold=1, window=60_000 ms
let mut mo = meta_oracle::new<DecimalValue>(1, 60_000, string::utf8(b"SUIUSD"));

// oracle_fresh  has timestamp = T       (now)
// oracle_stale  has timestamp = T - 3_600_000  (1 hour ago)

// Add fresh first, stale last → max_timestamp = T - 3_600_000
meta_oracle::add_simple_oracle(&mut mo, oracle_fresh);
meta_oracle::add_simple_oracle(&mut mo, oracle_stale);

// combine: min_timestamp = (T - 3_600_000) - 60_000 = T - 3_660_000
// Both oracles satisfy timestamp > T - 3_660_000 → both included
// median of [fresh_price, stale_price] is returned as "trusted"
let result = meta_oracle::median(mo);
// result contains stale price data despite the 60-second window
```

The root cause is at: [1](#0-0) 

The broken freshness cutoff derived from it: [2](#0-1) 

The financial use-case that is directly harmed: [3](#0-2)

### Citations

**File:** crates/sui-oracle/move/oracle/sources/meta_oracle.move (L38-44)
```text
    public fun add_simple_oracle<T: copy + drop + store>(meta_oracle: &mut MetaOracle<T>, oracle: &SimpleOracle) {
        let oracle_data = oracle::simple_oracle::get_latest_data(oracle, meta_oracle.ticker);
        if (option::is_some(&oracle_data)) {
            meta_oracle.max_timestamp = data::timestamp(option::borrow(&oracle_data));
        };
        vector::push_back(&mut meta_oracle.oracle_data, oracle_data);
    }
```

**File:** crates/sui-oracle/move/oracle/sources/meta_oracle.move (L51-67)
```text
    fun combine<T: copy + drop>(meta_oracle: MetaOracle<T>, ): (vector<T>, vector<address>) {
        let MetaOracle { mut oracle_data, threshold, time_window_ms, ticker: _, max_timestamp } = meta_oracle;
        let min_timestamp = max_timestamp - time_window_ms;
        let mut values = vector<T>[];
        let mut oracles = vector<address>[];
        while (vector::length(&oracle_data) > 0) {
            let oracle_data = vector::remove(&mut oracle_data, 0);
            if (option::is_some(&oracle_data)) {
                let oracle_data = option::destroy_some(oracle_data);
                if (data::timestamp(&oracle_data) > min_timestamp) {
                    vector::push_back(&mut values, *data::value(&oracle_data));
                    vector::push_back(&mut oracles, *data::oracle_address(&oracle_data));
                };
            };
        };
        assert!(vector::length(&values) >= threshold, EValidDataSizeLessThanThreshold);
        (values, oracles)
```

**File:** crates/sui-oracle/tests/data/Test/sources/test_module.move (L60-84)
```text
    public fun trusted_fx(
        oracle1: &SimpleOracle,
        oracle2: &SimpleOracle,
        oracle3: &SimpleOracle,
        mist_amount: u64,
        ctx: &mut TxContext
    ) {
        let mut meta_oracle = meta_oracle::new<DecimalValue>(3, 60000, string::utf8(b"SUIUSD"));
        meta_oracle::add_simple_oracle(&mut meta_oracle, oracle1);
        meta_oracle::add_simple_oracle(&mut meta_oracle, oracle2);
        meta_oracle::add_simple_oracle(&mut meta_oracle, oracle3);

        let trusted_data = meta_oracle::median(meta_oracle);
        let value = meta_oracle::value(&trusted_data);
        let decimals = decimal_value::decimal(value);
        let value = decimal_value::value(value);

        let amount = mist_amount * value;
        let usd = MockUSD {
            id: object::new(ctx),
            amount,
            decimals,
        };
        transfer::transfer(usd, tx_context::sender(ctx));
    }
```
