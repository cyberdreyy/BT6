### Title
`MetaOracle` Applies a Single `time_window_ms` Staleness Window Uniformly Across All Oracle Sources With Different Update Frequencies — (`File: crates/sui-oracle/move/oracle/sources/meta_oracle.move`)

---

### Summary

`MetaOracle<T>` in the Sui oracle framework stores exactly one `time_window_ms` field and applies it as the sole staleness gate for every `SimpleOracle` source added to the aggregator. Because different oracle providers update at different rates, a single window either silently accepts stale readings from fast-updating sources (if the window is widened to accommodate slow sources) or permanently excludes slow-updating sources from the quorum (if the window is tightened to match fast sources), triggering an `EValidDataSizeLessThanThreshold` abort on every call.

---

### Finding Description

`MetaOracle<T>` is declared with one staleness parameter shared by all sources:

```move
public struct MetaOracle<T> {
    oracle_data: vector<Option<Data<T>>>,
    threshold: u64,
    time_window_ms: u64,   // ← single window for every source
    ticker: String,
    max_timestamp: u64,
}
``` [1](#0-0) 

In `combine()`, every oracle reading is tested against the same derived `min_timestamp`:

```move
let min_timestamp = max_timestamp - time_window_ms;
...
if (data::timestamp(&oracle_data) > min_timestamp) {
    vector::push_back(&mut values, ...);
};
``` [2](#0-1) 

There is no per-source staleness override. Any caller who constructs a `MetaOracle` with `new()` and populates it via `add_simple_oracle()` is forced to pick one window for all sources.

A compounding defect exists in `add_simple_oracle`: `max_timestamp` is unconditionally overwritten with each successive oracle's timestamp rather than being kept as the running maximum:

```move
if (option::is_some(&oracle_data)) {
    meta_oracle.max_timestamp = data::timestamp(option::borrow(&oracle_data));
};
``` [3](#0-2) 

If oracles are added in decreasing-timestamp order, `max_timestamp` ends up as the *oldest* timestamp, making `min_timestamp` very small and allowing arbitrarily stale readings from earlier sources to pass the freshness check.

The canonical usage pattern in the test module confirms the design: three independent `SimpleOracle` objects are aggregated under a single 60 000 ms window with no per-source override:

```move
let mut meta_oracle = meta_oracle::new<DecimalValue>(3, 60000, string::utf8(b"SUIUSD"));
meta_oracle::add_simple_oracle(&mut meta_oracle, oracle1);
meta_oracle::add_simple_oracle(&mut meta_oracle, oracle2);
meta_oracle::add_simple_oracle(&mut meta_oracle, oracle3);
``` [4](#0-3) 

---

### Impact Explanation

Two mutually exclusive failure modes arise, identical to M-12:

| Window choice | Effect on slow source | Effect on fast source |
|---|---|---|
| Wide (≥ slowest heartbeat) | Passes freshness check | Stale data up to the slow heartbeat is silently accepted |
| Narrow (≤ fastest heartbeat) | Always filtered out; quorum falls below `threshold`; every call aborts with `EValidDataSizeLessThanThreshold` | Passes freshness check |

The abort path constitutes a **permanent DoS** for any on-chain protocol that relies on `MetaOracle::median()` for price discovery (e.g., collateral valuation, liquidation triggers). The stale-data path constitutes **harmful smart-contract behavior** because prices up to the slow oracle's full heartbeat period are consumed as if fresh.

---

### Likelihood Explanation

The `new()` and `add_simple_oracle()` functions are `public`, so any package publisher or ordinary user can construct a `MetaOracle`. Real-world oracle networks routinely mix sources with different update cadences (e.g., a 1-minute CEX feed alongside a 1-hour on-chain TWAP). The framework provides no mechanism to assign per-source windows, so any integrator who follows the documented pattern is exposed.

---

### Recommendation

1. **Per-source staleness windows**: Replace the single `time_window_ms` with a per-entry window, e.g. store `vector<u64>` alongside `oracle_data` and apply the corresponding window when filtering in `combine()`.

2. **Fix `max_timestamp` tracking**: Replace the unconditional assignment with a running maximum:
   ```move
   if (option::is_some(&oracle_data)) {
       let ts = data::timestamp(option::borrow(&oracle_data));
       if (ts > meta_oracle.max_timestamp) {
           meta_oracle.max_timestamp = ts;
       };
   };
   ``` [5](#0-4) 

---

### Proof of Concept

Scenario: two `SimpleOracle` sources for the same ticker — source A updates every 60 s, source B updates every 3 600 s (1 h). A `MetaOracle` is created with `threshold = 2` and `time_window_ms = 3_600_000`.

**Stale-data path**:
- At `t = 0`, both sources submit. `max_timestamp = 0` (B added last, overwrites A's timestamp — secondary bug).
- At `t = 3 599 000 ms`, source A submits a fresh reading. Source B has not updated.
- A new `MetaOracle` is constructed. `add_simple_oracle(oracle_A)` → `max_timestamp = 3_599_000`. `add_simple_oracle(oracle_B)` → `max_timestamp = 0` (B's stale timestamp overwrites A's).
- `min_timestamp = 0 − 3_600_000` → u64 underflow → **abort** (secondary bug surfaces here).

**Downtime path** (window set to fast source):
- `time_window_ms = 60_000`. Source B last updated 61 000 ms ago.
- `combine()` filters out B. `vector::length(&values) = 1 < threshold = 2`.
- `assert!(1 >= 2, EValidDataSizeLessThanThreshold)` → **abort on every call**. [6](#0-5)

### Citations

**File:** crates/sui-oracle/move/oracle/sources/meta_oracle.move (L20-26)
```text
    public struct MetaOracle<T> {
        oracle_data: vector<Option<Data<T>>>,
        threshold: u64,
        time_window_ms: u64,
        ticker: String,
        max_timestamp: u64,
    }
```

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

**File:** crates/sui-oracle/tests/data/Test/sources/test_module.move (L67-70)
```text
        let mut meta_oracle = meta_oracle::new<DecimalValue>(3, 60000, string::utf8(b"SUIUSD"));
        meta_oracle::add_simple_oracle(&mut meta_oracle, oracle1);
        meta_oracle::add_simple_oracle(&mut meta_oracle, oracle2);
        meta_oracle::add_simple_oracle(&mut meta_oracle, oracle3);
```
