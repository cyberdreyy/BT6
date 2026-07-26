### Title
Integer Underflow in `MetaOracle::combine()` Causes Unconditional Abort When All Oracles Have No Data — (`crates/sui-oracle/move/oracle/sources/meta_oracle.move`)

---

### Summary

The `combine()` function in `oracle::meta_oracle` computes `let min_timestamp = max_timestamp - time_window_ms` using bare unsigned subtraction. When every `SimpleOracle` added to the `MetaOracle` has no data for the requested ticker, `max_timestamp` stays at its initial value of `0`. Any non-zero `time_window_ms` then causes a u64 underflow, which Move aborts unconditionally. Instead of reaching the intended `EValidDataSizeLessThanThreshold` guard, the transaction aborts with an arithmetic error, producing a DoS on every contract that calls `median()` under this condition.

---

### Finding Description

`MetaOracle` is initialized with `max_timestamp: 0`: [1](#0-0) 

`add_simple_oracle` only updates `max_timestamp` when the oracle actually has data for the ticker: [2](#0-1) 

If every oracle returns `option::none()` (ticker not yet submitted, or data expired), `max_timestamp` remains `0`. Inside `combine()`, the very first statement is:

```move
let min_timestamp = max_timestamp - time_window_ms;   // 0 - 60_000 → underflow → abort
``` [3](#0-2) 

Move's unsigned arithmetic aborts on underflow before any further code runs. The threshold guard that was supposed to be the graceful failure path is never reached: [4](#0-3) 

The public entry point `median()` calls `combine()` directly, so any caller — including unprivileged users — can trigger this abort by invoking any contract that uses `MetaOracle::median()` while oracle data is absent: [5](#0-4) 

The test module `trusted_fx` demonstrates the intended public usage pattern: [6](#0-5) 

---

### Impact Explanation

Any on-chain contract that constructs a `MetaOracle` with a non-zero `time_window_ms` and calls `median()` will abort with an arithmetic underflow error — not the expected `EValidDataSizeLessThanThreshold` — whenever all backing `SimpleOracle` objects lack data for the requested ticker. This is a DoS on oracle-dependent contract logic reachable from public input. The condition arises naturally at deployment time (before any oracle has submitted data) and whenever all oracle data expires simultaneously.

---

### Likelihood Explanation

The condition is reachable by any ordinary caller without any privileged access. It occurs at every cold start of a new ticker and whenever all oracle feeds go stale at the same time. The `time_window_ms` parameter is always non-zero in practice (the test uses `60_000` ms), making the underflow path the default failure mode rather than an edge case.

---

### Recommendation

Guard the subtraction against underflow before computing `min_timestamp`:

```move
fun combine<T: copy + drop>(meta_oracle: MetaOracle<T>): (vector<T>, vector<address>) {
    let MetaOracle { mut oracle_data, threshold, time_window_ms, ticker: _, max_timestamp } = meta_oracle;
    // If max_timestamp < time_window_ms (including the initial 0 case), no data
    // can possibly be within the window; skip filtering and let the threshold
    // assert fire with the correct error code.
    let min_timestamp = if (max_timestamp >= time_window_ms) {
        max_timestamp - time_window_ms
    } else {
        0
    };
    ...
    assert!(vector::length(&values) >= threshold, EValidDataSizeLessThanThreshold);
    (values, oracles)
}
```

This ensures the threshold guard — not an arithmetic abort — is the observable failure when oracle data is absent.

---

### Proof of Concept

1. Deploy the `oracle` package.
2. Create three `SimpleOracle` objects but **do not** call `submit_data` on any of them for ticker `"SUIUSD"`.
3. Call `trusted_fx(oracle1, oracle2, oracle3, 1000, ctx)` from `test_module` (threshold = 3, `time_window_ms` = 60 000).
4. Inside `median()` → `combine()`, `max_timestamp = 0`, `time_window_ms = 60_000`.
5. `0u64 - 60_000u64` underflows → Move VM aborts the transaction with an arithmetic error before `EValidDataSizeLessThanThreshold` is ever checked.
6. The transaction fails with the wrong abort code, and any contract logic that depends on catching `EValidDataSizeLessThanThreshold` is bypassed.

### Citations

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

**File:** crates/sui-oracle/move/oracle/sources/meta_oracle.move (L51-53)
```text
    fun combine<T: copy + drop>(meta_oracle: MetaOracle<T>, ): (vector<T>, vector<address>) {
        let MetaOracle { mut oracle_data, threshold, time_window_ms, ticker: _, max_timestamp } = meta_oracle;
        let min_timestamp = max_timestamp - time_window_ms;
```

**File:** crates/sui-oracle/move/oracle/sources/meta_oracle.move (L66-67)
```text
        assert!(vector::length(&values) >= threshold, EValidDataSizeLessThanThreshold);
        (values, oracles)
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
