[1](#0-0)

### Citations

**File:** aptos-move/framework/aptos-stdlib/sources/data_structures/smart_vector.move (L31-48)
```text
    /// Create an empty vector using default logic to estimate `inline_capacity` and `bucket_size`, which may be
    /// inaccurate.
    /// This is exactly the same as empty() but is more standardized as all other data structures have new().
    public fun new<T: store>(): SmartVector<T> {
        empty()
    }

    #[deprecated]
    /// Create an empty vector using default logic to estimate `inline_capacity` and `bucket_size`, which may be
    /// inaccurate.
    public fun empty<T: store>(): SmartVector<T> {
        SmartVector {
            inline_vec: vector[],
            big_vec: option::none(),
            inline_capacity: option::none(),
            bucket_size: option::none(),
        }
    }
```
