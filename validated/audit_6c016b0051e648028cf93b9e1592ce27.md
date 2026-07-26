### Title
Missing Absolute Staleness Check in `meta_oracle::combine()` Allows Arbitrarily Old Price Data to Pass Validation — (File: `crates/sui-oracle/move/oracle/sources/meta_oracle.move`)

---

### Summary

The `oracle::meta_oracle` framework package contains two compounding flaws that allow arbitrarily stale price data to pass its freshness filter and be consumed by financial contracts. First, `add_simple_oracle()` tracks `max_timestamp` by unconditionally overwriting it with each successive oracle's timestamp rather than taking the running maximum. Second, `combine()` validates freshness by comparing oracle timestamps against a window derived from that incorrectly computed `max_timestamp`, with no reference to the current on-chain clock. The result is that if all contributing oracles hold old data, every data point passes the filter and the stale median price is returned as trusted.

---

### Finding Description

**Flaw 1 — `add_simple_oracle` does not track the true maximum timestamp.** [1](#0-0) 

Each call to `add_simple_oracle` unconditionally overwrites `meta_oracle.max_timestamp` with the current oracle's timestamp. If three oracles are added with timestamps `[T_high, T_mid, T_low]`, `max_timestamp` ends up as `T_low`, not `T_high`. The field is named `max_timestamp` but does not hold the maximum.

**Flaw 2 — `combine()` performs only a relative, clock-free staleness check.** [2](#0-1) 

`min_timestamp` is computed as `max_timestamp - time_window_ms`. Because `max_timestamp` is derived from the oracle data itself (not from `sui::clock::Clock`), this is a purely relative comparison: it filters out oracles whose timestamps differ from the last-added oracle by more than `time_window_ms`. If all oracles were last updated one hour ago, they all cluster within the window of each other and every data point passes. No check is ever made against the current block time.

**Flaw 3 — `get_latest_data` in `simple_oracle` returns data with no freshness check at all.** [3](#0-2) 

The `StoredData` struct carries a `timestamp` field set at submission time, but `get_latest_data` returns the stored value unconditionally. No `Clock` argument is accepted; no age bound is enforced.

**Consuming code uses the returned price directly for financial computation.** [4](#0-3) 

`simple_fx` calls `get_latest_data`, unwraps the `Option` (aborting only if no data exists at all), and multiplies the raw value by `mist_amount` to produce a USD amount — with no staleness guard between the fetch and the arithmetic.

---

### Impact Explanation

Any on-chain contract that uses `oracle::simple_oracle::get_latest_data` or `oracle::meta_oracle::median` to price assets or compute exchange rates will silently accept price data of arbitrary age. If the oracle operator stops submitting updates (due to downtime, network congestion, or a liveness failure), the last stored value remains indefinitely valid from the framework's perspective. Financial contracts built on this framework — such as the `MockUSD` minting pattern shown in `test_module` — will mint or transfer assets at a price that no longer reflects market reality, leading to incorrect valuations and potential fund loss for users.

This matches the "harmful smart-contract behavior" class in the Sui Allowed Impact Gate (Medium).

---

### Likelihood Explanation

The `SimpleOracle` is a shared object updated by a single privileged address. Any liveness interruption — planned maintenance, key rotation, network partition, or a slow Sui epoch — leaves the stored price frozen. Because the framework provides no absolute age bound, every downstream consumer inherits the vulnerability silently. An ordinary user calling a contract that uses this framework has no on-chain mechanism to detect or reject stale data.

---

### Recommendation

1. **Pass `&Clock` into `combine()` and `median()`** and assert that each accepted data point's timestamp is within an absolute maximum age of the current clock time:
   ```move
   use sui::clock::{Self, Clock};
   // inside combine():
   let now_ms = clock::timestamp_ms(clock);
   assert!(data::timestamp(&oracle_data) + max_age_ms >= now_ms, EStalePriceData);
   ```

2. **Fix `add_simple_oracle` to track the true maximum timestamp:**
   ```move
   if (option::is_some(&oracle_data)) {
       let ts = data::timestamp(option::borrow(&oracle_data));
       if (ts > meta_oracle.max_timestamp) {
           meta_oracle.max_timestamp = ts;
       };
   };
   ```

3. **Add a `Clock` parameter to `get_latest_data`** (or provide a separate `get_fresh_data` variant) that rejects data older than a configurable threshold before returning it to callers.

---

### Proof of Concept

1. Deploy the `oracle` package and create a `SimpleOracle` shared object.
2. Call `submit_data` once with a `DecimalValue` price at time `T`.
3. Wait (or simulate) until the on-chain clock advances by more than `time_window_ms` (e.g., 60 seconds).
4. Call `trusted_fx` (or any contract using `meta_oracle::median`) passing the three now-stale oracle objects.
5. `add_simple_oracle` sets `max_timestamp = T` for each oracle; `combine` computes `min_timestamp = T - 60000`; all three data points satisfy `T > T - 60000`; the threshold check `vector::length(&values) >= threshold` passes; `median` returns the stale price.
6. The financial calculation proceeds with the hour-old price, producing an incorrect `MockUSD` amount with no abort or warning. [5](#0-4) [1](#0-0) [3](#0-2)

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

**File:** crates/sui-oracle/tests/data/Test/sources/test_module.move (L44-58)
```text
    public fun simple_fx(oracle: &SimpleOracle, mist_amount: u64, ctx: &mut TxContext) {
        let single_data = simple_oracle::get_latest_data<DecimalValue>(oracle, string::utf8(b"SUIUSD"));
        let single_data = option::destroy_some(single_data);
        let value = data::value(&single_data);
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
