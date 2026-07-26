### Title
`add_simple_oracle` Overwrites `max_timestamp` With Last-Added Oracle Instead of Tracking the Maximum, Allowing Stale Data Inclusion in `MetaOracle` Median — (`File: crates/sui-oracle/move/oracle/sources/meta_oracle.move`)

---

### Summary

`MetaOracle.add_simple_oracle` unconditionally overwrites `max_timestamp` with each oracle's timestamp rather than tracking the running maximum. Because `combine` derives the staleness cutoff as `min_timestamp = max_timestamp - time_window_ms`, the effective time window is anchored to the **last oracle added**, not the most recent one. A caller who controls oracle ordering can force stale oracle data to pass the freshness filter and be included in the median, directly analogous to the short-TWAP manipulation described in the external report.

---

### Finding Description

In `meta_oracle.move`, `add_simple_oracle` is:

```move
public fun add_simple_oracle<T: copy + drop + store>(
    meta_oracle: &mut MetaOracle<T>,
    oracle: &SimpleOracle
) {
    let oracle_data = oracle::simple_oracle::get_latest_data(oracle, meta_oracle.ticker);
    if (option::is_some(&oracle_data)) {
        meta_oracle.max_timestamp = data::timestamp(option::borrow(&oracle_data));
    };
    vector::push_back(&mut meta_oracle.oracle_data, oracle_data);
}
``` [1](#0-0) 

The assignment `meta_oracle.max_timestamp = data::timestamp(...)` replaces the stored value on every call. It should instead be a conditional `if new_ts > max_timestamp { max_timestamp = new_ts }`. As written, `max_timestamp` ends up holding the timestamp of whichever oracle was added **last** (and had data), not the maximum across all oracles.

`combine` then computes the staleness cutoff from this wrong anchor:

```move
let min_timestamp = max_timestamp - time_window_ms;
...
if (data::timestamp(&oracle_data) > min_timestamp) {
    vector::push_back(&mut values, *data::value(&oracle_data));
``` [2](#0-1) 

**Concrete broken invariant:**

Suppose three oracles have timestamps `T_fresh = 1_000_000`, `T_mid = 500_000`, `T_stale = 100_000` (ms), and `time_window_ms = 60_000`.

| Oracle addition order | `max_timestamp` | `min_timestamp` | Oracles that pass filter |
|---|---|---|---|
| `[fresh, mid, stale]` (stale last) | 100 000 | 40 000 | **all three** (stale data included) |
| `[stale, mid, fresh]` (fresh last) | 1 000 000 | 940 000 | **only fresh** (correct) |

When a stale oracle is added last, `min_timestamp` drops to `T_stale - time_window_ms`, which is far in the past. Every oracle's data passes the `> min_timestamp` check, including data that is many multiples of `time_window_ms` old. The median is then computed over a polluted set that includes arbitrarily stale readings.

The `MetaOracle` struct is not stored on-chain; it is constructed per-transaction. Any Move function that accepts `&SimpleOracle` arguments from the caller (as `trusted_fx` does) lets the caller choose both which oracles to supply and in what order:

```move
public fun trusted_fx(
    oracle1: &SimpleOracle,
    oracle2: &SimpleOracle,
    oracle3: &SimpleOracle,
    ...
) {
    let mut meta_oracle = meta_oracle::new<DecimalValue>(3, 60000, ...);
    meta_oracle::add_simple_oracle(&mut meta_oracle, oracle1);
    meta_oracle::add_simple_oracle(&mut meta_oracle, oracle2);
    meta_oracle::add_simple_oracle(&mut meta_oracle, oracle3); // last → sets max_timestamp
``` [3](#0-2) 

Because `simple_oracle::create` is a permissionless public entry function, any user can create a `SimpleOracle`, submit any value to it at any time, and then pass it as the last argument to a consuming function:

```move
public entry fun create(name: String, url: String, description: String, ctx: &mut TxContext) {
    let oracle = SimpleOracle { id: object::new(ctx), address: tx_context::sender(ctx), ... };
    transfer::share_object(oracle)
}
``` [4](#0-3) 

The attacker's own oracle carries a stale timestamp and a manipulated price. By placing it last in the call, they anchor `max_timestamp` to their stale timestamp, widen the acceptance window, and ensure their manipulated value is included in the median alongside legitimate oracles.

A secondary consequence: if the last oracle's timestamp is smaller than `time_window_ms` (e.g., a freshly created oracle with timestamp near 0), the subtraction `max_timestamp - time_window_ms` underflows and the transaction aborts, producing a denial-of-service against any protocol that relies on this oracle for critical operations.

---

### Impact Explanation

**Harmful smart-contract behavior (Medium/High):** Any protocol that uses `MetaOracle.median` for price-sensitive decisions (collateral valuation, liquidation thresholds, FX conversion) and accepts oracle objects as caller-supplied parameters can have its price feed polluted with stale, attacker-controlled data. The median is computed over a wider-than-intended set, reducing the resistance to price manipulation — the same root cause as the external Uniswap TWAP report.

**DoS / permanent fund lock (Medium):** If the last oracle supplied has a timestamp smaller than `time_window_ms`, `combine` aborts unconditionally. A protocol that gates withdrawals or repayments on a successful oracle read can be permanently bricked for affected users.

---

### Likelihood Explanation

Likelihood is **Low-to-Medium**. Exploitation requires:
1. A consuming protocol that accepts oracle object references as user-supplied transaction arguments (as the reference `trusted_fx` does).
2. The attacker to create and control a `SimpleOracle` with a stale timestamp and a manipulated value — both are permissionless operations.
3. The attacker to pass their oracle as the last argument.

No privileged role, validator collusion, or governance action is required. The attack is executable by any ordinary SUI holder in a single transaction.

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
``` [5](#0-4) 

Additionally, add a guard in `combine` to prevent underflow when `max_timestamp < time_window_ms`:

```move
let min_timestamp = if (max_timestamp > time_window_ms) {
    max_timestamp - time_window_ms
} else { 0 };
``` [6](#0-5) 

---

### Proof of Concept

```move
// Attacker creates their own oracle with a stale timestamp and manipulated price
// (permissionless — anyone can call simple_oracle::create)
// Attacker submits a manipulated value to their oracle at time T_stale

// Victim protocol exposes:
// public fun get_price(oracle1: &SimpleOracle, oracle2: &SimpleOracle, attacker_oracle: &SimpleOracle, ...)

// Attacker calls get_price passing their stale oracle as the LAST argument.
// Inside get_price:
//   add_simple_oracle(meta, oracle1)         // max_timestamp = T_fresh (e.g. 1_000_000)
//   add_simple_oracle(meta, oracle2)         // max_timestamp = T_mid   (e.g.   500_000)
//   add_simple_oracle(meta, attacker_oracle) // max_timestamp = T_stale (e.g.   100_000)
//
// combine():
//   min_timestamp = 100_000 - 60_000 = 40_000
//   oracle1 (ts=1_000_000 > 40_000) → INCLUDED ✓
//   oracle2 (ts=  500_000 > 40_000) → INCLUDED ✓
//   attacker (ts=100_000  > 40_000) → INCLUDED ✓  ← should have been excluded
//
// median([legitimate_price, legitimate_price, manipulated_price])
// With threshold=3 and 3 values, the median is the middle value.
// Attacker sets manipulated_price between the two legitimate prices to shift the median.
``` [7](#0-6) [8](#0-7)

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

**File:** crates/sui-oracle/move/oracle/sources/simple_oracle.move (L61-64)
```text
    public entry fun create(name: String, url: String, description: String, ctx: &mut TxContext) {
        let oracle = SimpleOracle { id: object::new(ctx), address: tx_context::sender(ctx), name, description, url };
        transfer::share_object(oracle)
    }
```

**File:** crates/sui-oracle/move/oracle/sources/simple_oracle.move (L66-74)
```text
    public entry fun submit_data<T: store + copy + drop>(
        oracle: &mut SimpleOracle,
        clock: &Clock,
        ticker: String,
        value: T,
        identifier: String,
        ctx: &mut TxContext
    ) {
        assert!(oracle.address == tx_context::sender(ctx), ESenderNotOracle);
```
