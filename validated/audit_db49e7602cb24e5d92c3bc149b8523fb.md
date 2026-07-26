### Title
`MetaOracle` Staleness Check Uses Relative Timestamps Instead of Wall-Clock Time, Allowing Arbitrarily Stale Prices to Pass as Fresh — (`File: crates/sui-oracle/move/oracle/sources/meta_oracle.move`)

---

### Summary

The `oracle::meta_oracle` Move package, which is the on-chain price-aggregation layer of the Sui oracle framework, performs its staleness filter using a relative comparison between oracle feed timestamps rather than comparing against the current wall-clock time (`sui::clock::Clock`). When all oracle feeds are stale simultaneously (e.g., the off-chain feeder node stops), every feed passes the freshness gate and the `MetaOracle` returns arbitrarily old prices as "trusted" data. This is the direct Sui analog of the Chainlink `latestAnswer()` / no-staleness-check pattern.

---

### Finding Description

**`get_latest_data` — no staleness check at all**

`simple_oracle::get_latest_data` is the primitive read function. It accepts no `Clock` argument and performs zero freshness validation before returning the stored price. [1](#0-0) 

The `StoredData` struct does carry a `timestamp` field, but the function ignores it entirely. [2](#0-1) 

**`add_simple_oracle` — `max_timestamp` is the timestamp of the *last* oracle added, not the true maximum**

Each call to `add_simple_oracle` unconditionally overwrites `max_timestamp` with the current oracle's timestamp. If oracles are added in non-monotone order, `max_timestamp` is not the maximum across all feeds; it is simply the timestamp of whichever feed was added last. [3](#0-2) 

**`combine` — staleness window is anchored to `max_timestamp`, not to `clock::timestamp_ms`**

The freshness gate computes `min_timestamp = max_timestamp - time_window_ms` and accepts any feed whose timestamp exceeds that floor. Because `max_timestamp` is itself a stored oracle timestamp (not the current time), if every oracle feed is stale the entire window slides into the past and every feed passes. [4](#0-3) 

No `Clock` object is accepted anywhere in `MetaOracle::new`, `add_simple_oracle`, `combine`, or `median`, so there is no path to an absolute freshness check within the framework. [5](#0-4) 

---

### Impact Explanation

Any on-chain contract that calls `simple_oracle::get_latest_data` directly, or that builds a `MetaOracle` and calls `median`, will silently consume arbitrarily old prices whenever the off-chain feeder stops publishing. Because the `MetaOracle` is the intended "trusted" aggregation layer, downstream DeFi logic (FX conversion, collateral valuation, liquidation triggers) will operate on stale data without any on-chain signal that the data is stale. This constitutes harmful smart-contract behavior (Medium impact under the HackenProof gate).

---

### Likelihood Explanation

The off-chain `DataProvider` / `OnChainDataUploader` pipeline has its own staleness tolerance, but that guard lives entirely off-chain. [6](#0-5) 

Any network disruption, node crash, or deliberate halt of the feeder process causes the on-chain `StoredData` to age indefinitely. Because `get_latest_data` and `MetaOracle` impose no on-chain age limit, every subsequent consumer transaction will use the last-written (stale) value. The trigger is reachable by any ordinary user who calls a consumer entry function.

---

### Recommendation

1. **Add a `Clock` parameter to `get_latest_data`** and assert `clock::timestamp_ms(clock) - stored_timestamp <= max_age_ms` before returning data.
2. **Fix `add_simple_oracle`** to track the true maximum timestamp across all feeds (use `if timestamp > meta_oracle.max_timestamp { meta_oracle.max_timestamp = timestamp }`).
3. **Pass `&Clock` into `combine` / `median`** and anchor `min_timestamp` to `clock::timestamp_ms(clock) - time_window_ms` rather than to `max_timestamp - time_window_ms`.

---

### Proof of Concept

```move
// Scenario: oracle operator stops updating at T=1000 ms.
// time_window_ms = 60_000 ms.
// At T = 3_600_000 ms (1 hour later), a user calls trusted_fx.

let mut meta = meta_oracle::new<DecimalValue>(1, 60_000, utf8(b"SUIUSD"));
// oracle1 last updated at T=1000 (stale by 3599 seconds)
meta_oracle::add_simple_oracle(&mut meta, oracle1);
// max_timestamp = 1000

// combine():
//   min_timestamp = 1000 - 60_000 → underflow / wraps to u64::MAX (abort)
//   OR if max_timestamp >= time_window_ms:
//   min_timestamp = 1000 - 60_000 = -59_000 (treated as 0 in practice)
//   oracle1.timestamp (1000) > 0  → PASSES
// median() returns the 1-hour-old price as "trusted".
```

Because `add_simple_oracle` does not accept a `Clock`, there is no on-chain mechanism to reject the stale feed. The `trusted_fx` consumer in the reference test module confirms the pattern: [7](#0-6)

### Citations

**File:** crates/sui-oracle/move/oracle/sources/simple_oracle.move (L29-35)
```text
    public struct StoredData<T: store> has copy, store, drop {
        value: T,
        sequence_number: u64,
        timestamp: u64,
        /// An identifier for the reading (for example real time of observation, or sequence number of observation on other chain).
        identifier: String,
    }
```

**File:** crates/sui-oracle/move/oracle/sources/simple_oracle.move (L51-58)
```text
    public fun get_latest_data<T: store + copy>(oracle: &SimpleOracle, ticker: String): Option<Data<T>> {
        if (!df::exists_(&oracle.id, ticker)) {
            return option::none()
        };
        let data: &StoredData<T> = df::borrow(&oracle.id, ticker);
        let StoredData { value, sequence_number, timestamp, identifier } = *data;
        option::some(data::new(value, ticker, sequence_number, timestamp, oracle.address, identifier))
    }
```

**File:** crates/sui-oracle/move/oracle/sources/meta_oracle.move (L28-36)
```text
    public fun new<T: copy + drop>(threshold: u64, time_window_ms: u64, ticker: String): MetaOracle<T> {
        MetaOracle {
            oracle_data: vector[],
            threshold,
            time_window_ms,
            ticker,
            max_timestamp: 0,
        }
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

**File:** crates/sui-oracle/src/lib.rs (L380-399)
```rust
            let staleness_tolerance =
                self.staleness_tolerance.get(feed_name).unwrap_or_else(|| {
                    panic!("Bug, missing staleness tolerance for feed: {}", feed_name)
                });
            let duration_since = data_point.retrieval_instant.elapsed();
            if duration_since > staleness_tolerance.add(Duration::from_secs(1)) {
                warn!(
                    feed_name,
                    value = data_point.value,
                    ?duration_since,
                    ?staleness_tolerance,
                    "Data is too stale, skipping."
                );
                self.metrics
                    .data_staleness
                    .with_label_values(&[feed_name])
                    .inc();
            } else {
                data_points.push(data_point);
            }
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
