### Title
`MetaOracle.combine` Accepts Arbitrarily Stale Oracle Data Due to Missing Absolute Clock-Time Staleness Check — (`crates/sui-oracle/move/oracle/sources/meta_oracle.move`)

---

### Summary

The `combine` function in `meta_oracle.move` performs only a *relative* inter-oracle staleness check (`data.timestamp > max_timestamp - time_window_ms`) and never compares any oracle timestamp against the current on-chain clock. As a result, a set of oracle readings that are hours or days old will pass the freshness gate as long as they are mutually consistent within `time_window_ms`. A secondary compounding bug in `add_simple_oracle` means `max_timestamp` is not the actual maximum across all oracles but merely the timestamp of the *last* oracle added, further distorting the window anchor.

---

### Finding Description

**Primary defect — no absolute clock check in `combine`** [1](#0-0) 

```move
fun combine<T: copy + drop>(meta_oracle: MetaOracle<T>): (vector<T>, vector<address>) {
    let MetaOracle { mut oracle_data, threshold, time_window_ms, ticker: _, max_timestamp } = meta_oracle;
    let min_timestamp = max_timestamp - time_window_ms;   // ← relative only
    ...
    if (data::timestamp(&oracle_data) > min_timestamp) {  // ← no clock comparison
```

Neither `combine` nor its only public caller `median` accepts a `Clock` argument. [2](#0-1) 

The check `data.timestamp > max_timestamp - time_window_ms` only verifies that readings are within `time_window_ms` of each other. If all three oracles last updated at epoch-ms `T` and the current clock is `T + 3 600 000` (one hour later), every reading still satisfies the predicate because `T > T - time_window_ms` is trivially true.

**Secondary defect — `max_timestamp` is not the actual maximum** [3](#0-2) 

```move
public fun add_simple_oracle<T: copy + drop + store>(meta_oracle: &mut MetaOracle<T>, oracle: &SimpleOracle) {
    let oracle_data = oracle::simple_oracle::get_latest_data(oracle, meta_oracle.ticker);
    if (option::is_some(&oracle_data)) {
        meta_oracle.max_timestamp = data::timestamp(option::borrow(&oracle_data)); // overwrites, not max()
    };
```

Each call unconditionally overwrites `max_timestamp` with the current oracle's timestamp. If oracles are added in order `[ts=1000, ts=500]`, `max_timestamp` ends up as `500`, not `1000`. The staleness window is then anchored to the wrong (lower) value, which can either silently accept data that should be filtered or cause an underflow abort when `max_timestamp < time_window_ms`.

**Intended usage pattern (from the reference integration)** [4](#0-3) 

`trusted_fx` constructs a `MetaOracle` with `time_window_ms = 60_000` ms, aggregates three `SimpleOracle` feeds, calls `median`, and uses the resulting price to mint a `MockUSD` token proportional to `mist_amount * price`. This is a direct financial operation whose correctness depends entirely on price freshness.

---

### Impact Explanation

Any on-chain protocol that calls `meta_oracle::median` to price assets or collateral will silently consume stale data whenever oracle operators are delayed or offline. In a lending or DEX context this enables:

- **Stale-price arbitrage**: borrow or swap at a price that no longer reflects market reality, extracting value from the protocol's liquidity pool or collateral vault.
- **Incorrect liquidation avoidance / triggering**: a position that should be liquidated (or should not be) is evaluated against an hours-old price.

This constitutes **harmful smart-contract behavior** (High/Medium under the active HackenProof Sui gate).

---

### Likelihood Explanation

Oracle operators can be delayed by network congestion, keeper downtime, or gas spikes. An attacker needs only to observe that the on-chain `SimpleOracle` objects have not been updated recently and then submit a transaction that constructs a `MetaOracle` from those stale objects. No privileged access is required; any ordinary SUI holder can call `meta_oracle::new`, `add_simple_oracle`, and `median` in a single PTB.

---

### Recommendation

1. Add a `clock: &Clock` parameter to both `combine` and `median`.
2. Inside `combine`, assert that `max_timestamp >= clock::timestamp_ms(clock) - max_absolute_staleness_ms` before computing `min_timestamp`.
3. Fix `add_simple_oracle` to track the true maximum: replace the unconditional assignment with `if (ts > meta_oracle.max_timestamp) { meta_oracle.max_timestamp = ts; }`.
4. Expose `max_absolute_staleness_ms` as a constructor parameter (analogous to the fix in the referenced Isomorph report) so each deployment can tune it to the actual heartbeat of its price feeds.

---

### Proof of Concept

```
Time T=1_700_000_000_000 ms  (epoch):
  oracle1.submit_data("SUIUSD", price=1.20)   → stored ts = T
  oracle2.submit_data("SUIUSD", price=1.21)   → stored ts = T
  oracle3.submit_data("SUIUSD", price=1.19)   → stored ts = T

Time T + 3_600_000 ms  (one hour later, SUI spot = 0.80):
  Attacker PTB:
    meta = meta_oracle::new(3, 60_000, "SUIUSD")
    meta_oracle::add_simple_oracle(&mut meta, oracle1)  // max_timestamp = T
    meta_oracle::add_simple_oracle(&mut meta, oracle2)  // max_timestamp = T (overwrite)
    meta_oracle::add_simple_oracle(&mut meta, oracle3)  // max_timestamp = T (overwrite)
    // combine: min_timestamp = T - 60_000
    // all three readings have ts = T  >  T - 60_000  → PASS
    trusted = meta_oracle::median(meta)                 // returns stale price ≈ 1.20
    // attacker mints MockUSD at 1.20 while true price is 0.80
    // 50% over-valuation of SUI → protocol drained
```

The attack requires no privileged role: the attacker is an ordinary SUI holder who observes stale `SimpleOracle` state and submits a single programmable transaction.

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

**File:** crates/sui-oracle/move/oracle/sources/meta_oracle.move (L51-68)
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
    }
```

**File:** crates/sui-oracle/move/oracle/sources/meta_oracle.move (L71-77)
```text
    public fun median<T: copy + drop>(meta_oracle: MetaOracle<T>): TrustedData<T> {
        let (values, oracles) = combine(meta_oracle);
        let mut sortedData = quick_sort(values);
        let i = vector::length(&sortedData) / 2;
        let value = vector::remove(&mut sortedData, i);
        TrustedData { value, oracles }
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
