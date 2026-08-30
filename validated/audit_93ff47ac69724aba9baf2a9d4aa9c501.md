No vulnerability found for this question.

The code path confirmed: `bandwidth_requests.shards_bandwidth_requests` is a `BTreeMap<ShardId, BandwidthRequests>` iterated in deterministic key order, and within each shard the `requests: Vec<BandwidthRequest>` (from `BandwidthRequestsV1`) is a `Vec`, iterated in insertion order [1](#0-0) . No `HashMap` appears anywhere in this construction chain — `ShardIndexMap` and `ShardLinkMap` are explicitly array-backed deterministic structures used as `BTreeMap` substitutes [2](#0-1) , and the tie-break bucket itself is a `BTreeMap<Bandwidth, Vec<SchedulerBandwidthRequest>>` [3](#0-2) .

This is not an exploitable finding for an unprivileged attacker: the `rng_seed` comes from `prev_block_hash`, which is identical and canonical for all validators applying the same block, and the pre-shuffle `Vec` order is fully determined by the deterministic `BTreeMap` shard-id iteration plus the fixed request-generation order within each shard's own bandwidth request list — nothing an ordinary client controls (transaction contents, gas, deposits, access keys) can alter that iteration order across honest nodes. Since every honest validator constructs the identical `Vec<SchedulerBandwidthRequest>` in the identical order before calling `shuffle`, there is no reachable state-root divergence; the "attack" described (two shards landing in the same allowance bucket) only affects which of the two *equally-eligible* requests wins the tie-break, which is an intended, deterministic, and consensus-safe outcome, not a fork. No caller path was found that introduces `HashMap`-based non-determinism into this chain.

### Citations

**File:** runtime/runtime/src/bandwidth_scheduler/scheduler.rs (L253-276)
```rust
        // Convert bandwidth requests to representation used in the algorithm.
        let mut scheduler_bandwidth_requests: Vec<SchedulerBandwidthRequest> = Vec::new();
        for (sender_shard, shard_bandwidth_requests) in
            &bandwidth_requests.shards_bandwidth_requests
        {
            let requests = match shard_bandwidth_requests {
                BandwidthRequests::V1(requests_v1) => &requests_v1.requests,
            };

            for bandwidth_request in requests {
                // Convert request to the internal representation. It might turn out that the
                // request isn't applicable (e.g. shard ids from other layout, too little bandwidth
                // requested), in which case the function returns `None` and the request is ignored.
                // TODO(bandwidth_scheduler) - add a warning?
                if let Some(request) = SchedulerBandwidthRequest::new(
                    *sender_shard,
                    bandwidth_request,
                    params,
                    &shard_layout,
                ) {
                    scheduler_bandwidth_requests.push(request);
                }
            }
        }
```

**File:** runtime/runtime/src/bandwidth_scheduler/scheduler.rs (L347-356)
```rust
    fn process_bandwidth_requests(&mut self, requests: Vec<SchedulerBandwidthRequest>) {
        // Bandwidth requests, ordered by link allowance.
        let mut requests_by_allowance: BTreeMap<Bandwidth, Vec<SchedulerBandwidthRequest>> =
            BTreeMap::new();
        for request in requests {
            requests_by_allowance
                .entry(self.get_allowance(&request.link))
                .or_insert_with(Vec::new)
                .push(request);
        }
```

**File:** runtime/runtime/src/bandwidth_scheduler/scheduler.rs (L660-710)
```rust
/// Equivalent to BTreeMap<ShardIndex, T>
/// Accessing a value is done by indexing into an array, which is faster than a lookup in BTreeMap or HashMap.
/// Should be used only with indexes from the same layout that was given in the constructor!
pub struct ShardIndexMap<T> {
    data: Vec<Option<T>>,
}

impl<T> ShardIndexMap<T> {
    pub fn new(layout: &ShardLayout) -> Self {
        let num_indexes: usize =
            layout.num_shards().try_into().expect("num_shards doesn't fit into usize");
        let mut data = Vec::with_capacity(num_indexes);
        // T might not implement Clone, so we can't use vec![None; mapping.indexes_len()]
        for _ in 0..num_indexes {
            data.push(None);
        }
        Self { data }
    }

    pub fn get(&self, index: &ShardIndex) -> Option<&T> {
        self.data[*index].as_ref()
    }

    pub fn get_mut(&mut self, index: &ShardIndex) -> Option<&mut T> {
        self.data[*index].as_mut()
    }

    pub fn insert(&mut self, index: ShardIndex, value: T) {
        self.data[index] = Some(value);
    }
}

/// Equivalent to BTreeMap<ShardLink, T>
/// Accessing a value is done by indexing into an array, which is faster than a lookup in BTreeMap or HashMap.
/// Should be used only with indexes from the same layout that was given in the constructor!
pub struct ShardLinkMap<T> {
    data: Vec<Option<T>>,
    num_indexes: usize,
}

impl<T> ShardLinkMap<T> {
    pub fn new(layout: &ShardLayout) -> Self {
        let num_indexes: usize =
            layout.num_shards().try_into().expect("Can't convert u64 to usize");
        let data_len = num_indexes * num_indexes;
        let mut data = Vec::with_capacity(data_len);
        for _ in 0..data_len {
            data.push(None);
        }
        Self { data, num_indexes }
    }
```
