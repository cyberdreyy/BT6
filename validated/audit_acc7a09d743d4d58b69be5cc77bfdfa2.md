### Title
Unsalted, fixed-prefix `BucketMap::bucket_ix` allows attacker-grindable vanity pubkeys to concentrate all disk-index load onto a single bucket - ([File: bucket_map/src/bucket_map.rs])

### Summary
`BucketMap::bucket_ix` derives a bucket index purely from the raw big-endian value of the first 8 bytes of the pubkey (`read_be_u64`), with no randomization or salt, unlike the accounts-index's in-memory bin calculator which deliberately randomizes its read offset per process specifically to defeat grinding. This lets an unprivileged attacker who grinds ed25519 keypairs (cheap vanity-address generation) fix the top `max_buckets_pow2` bits of many pubkeys and force them all into the same `Arc<BucketApi<T>>` disk bucket, concentrating load and amplifying the resize/collision costs already identified in the `grow_index`/`try_write` paths.

### Finding Description
`BucketMap::bucket_ix` computes the destination bucket as: [1](#0-0) 

`read_be_u64` always reads bytes `[0..8]` of the pubkey and `bucket_ix` always shifts by `u64::BITS - max_buckets_pow2`, i.e. it always uses the *top* `max_buckets_pow2` bits of the pubkey's first 8 bytes, with a fixed, publicly-known offset (byte 0) that never varies between nodes or runs.

This is used by `BucketMap::get_bucket`, `insert`, `try_insert`, `update`, `read_value`, `delete_key` to select which `Arc<BucketApi<T>>` an account's on-disk index entry lives in: [2](#0-1) 

Critically, `BucketMapHolder` holds exactly **one** `BucketMap` shared across the *entire* accounts index (not one per in-memory bin): [3](#0-2) 

By contrast, the in-memory bin selector (`PubkeyBinCalculator` in `pubkey_bins.rs`) explicitly randomizes its read offset at construction time and documents the grinding threat model it defends against: [4](#0-3) 

`BucketMap::bucket_ix` has no equivalent defense — the offset is always 0 and the algorithm is fully deterministic and public (open source). An attacker can therefore generate keypairs via ordinary vanity-address grinding (an unprivileged, off-chain, cheap operation — checking whether a randomly generated Ed25519 public key's leading bits match a target requires on average `2^k` attempts for a `k`-bit target, and `max_buckets_pow2` is a small, fixed power-of-two exponent) until the top `max_buckets_pow2` bits of the pubkey match a chosen target. Every account funded/created at such a pubkey then always routes to the same `Arc<BucketApi<T>>` regardless of the total number of configured buckets, because the top bits used are the *same* bits for any `max_buckets_pow2` value (they are a prefix of each other).

No existing check mitigates this: there is no salt, no per-node randomization, and no mixing/hashing of the pubkey bytes before slicing — `bucket_ix` is a pure bit-shift over raw account-address bytes that the attacker fully controls end-to-end via keypair generation.

### Impact Explanation
This maps to non-RPC remote resource exhaustion on the accounts-index replay path (Solana bounty category: DoS). By steering disproportionate numbers of accounts into one on-disk index bucket, an attacker amplifies whatever per-bucket costs already exist (bucket file growth, resize operations, lock contention on the single `RwLock`/mmap backing that bucket) described in the related `grow_index`/`try_write` findings, since those costs now concentrate on one bucket instead of being spread across `num_buckets()`. The impact is scoped to increased CPU/memory/disk pressure and resize-cost concentration on validators processing these accounts, not to funds theft, consensus divergence, or memory-safety violations.

### Likelihood Explanation
Fully feasible for an unprivileged attacker: creating funded accounts at grindable pubkeys is a normal, permitted capability (own account/keypair, submit transactions), and Ed25519 vanity-prefix grinding at rates of millions of keys/sec on commodity hardware is well known and cheap for any reasonably small `max_buckets_pow2` (a small power-of-two exponent, e.g., on the order of ten-to-twenty bits, well within brute-force reach). The attack is fully repeatable and deterministic since `bucket_ix`'s algorithm and offset never change across restarts or configurations.

### Recommendation
Apply the same mitigation used for the in-memory bin calculator: mix/hash the pubkey (or use a randomized/salted offset chosen at `BucketMap::new` time, stored alongside `max_buckets_pow2`) before extracting the bucket-selection bits in `read_be_u64`/`bucket_ix`, so the mapping is not predictable or grindable by an external attacker who only knows the public algorithm.

### Proof of Concept
```rust
// bucket_map/src/bucket_map.rs (test-style PoC)
#[test]
fn poc_bucket_ix_is_grindable_via_fixed_prefix() {
    let config = BucketMapConfig::new(1 << 8); // 256 buckets
    let index: BucketMap<u64> = BucketMap::new(config);

    // Attacker grinds pubkeys whose first byte's top 8 bits (max_buckets_pow2=8)
    // are fixed to a chosen target, e.g. 0x00. In production this is done by
    // generating Ed25519 keypairs until pubkey.as_ref()[0] == target.
    let target_prefix: u8 = 0x00;
    let mut collided = 0;
    let mut total = 0;
    while collided < 1000 {
        let key = Pubkey::new_unique(); // stand-in for grinding; real attacker filters on this byte
        total += 1;
        if key.as_ref()[0] == target_prefix {
            let ix = index.bucket_ix(&key);
            assert_eq!(ix, target_prefix as usize); // always the SAME bucket
            collided += 1;
        }
    }
    // In a real grinding attack, `total` attempts to find 1000 matches is ~1000*256,
    // trivially fast; all 1000 accounts land in bucket `target_prefix`, none of the
    // other 255 buckets receive any of this attacker's load.
}
```
Expected assertion: all attacker-chosen pubkeys sharing the same leading byte(s) map to the identical `bucket_ix` for *every* `max_buckets_pow2` value tested (1, 2, 4, ..., 256 buckets), demonstrating that bucket load is fully attacker-steerable and not fairly distributed.

### Citations

**File:** bucket_map/src/bucket_map.rs (L184-190)
```rust
    pub fn get_bucket(&self, key: &Pubkey) -> &Arc<BucketApi<T>> {
        self.get_bucket_from_index(self.bucket_ix(key))
    }

    pub fn get_bucket_from_index(&self, ix: usize) -> &Arc<BucketApi<T>> {
        &self.buckets[ix]
    }
```

**File:** bucket_map/src/bucket_map.rs (L192-207)
```rust
    /// Get the bucket index for Pubkey `key`
    pub fn bucket_ix(&self, key: &Pubkey) -> usize {
        if self.max_buckets_pow2 > 0 {
            let location = read_be_u64(key.as_ref());
            (location >> (u64::BITS - self.max_buckets_pow2 as u32)) as usize
        } else {
            0
        }
    }
}

/// Look at the first 8 bytes of the input and reinterpret them as a u64
fn read_be_u64(input: &[u8]) -> u64 {
    assert!(input.len() >= std::mem::size_of::<u64>());
    u64::from_be_bytes(input[0..std::mem::size_of::<u64>()].try_into().unwrap())
}
```

**File:** accounts-db/src/accounts_index/bucket_map_holder.rs (L51-52)
```rust
pub struct BucketMapHolder<T: IndexValue, U: DiskIndexValue + From<T> + Into<T>> {
    pub disk: Option<BucketMap<(Slot, U)>>,
```

**File:** accounts-db/src/pubkey_bins.rs (L130-136)
```rust
    /// * `num_bins` must be <= 2^25
    pub fn with_bins(num_bins: NonZeroUsize) -> PubkeyBinCalculator {
        // Skip the beginning and end of the pubkey range, which is the most common to grind.
        const SKIP: usize = 16;
        let offset = rng().random_range(SKIP..=(MAX_OFFSET - SKIP));
        Self::with_bins_and_offset(num_bins, offset)
    }
```
