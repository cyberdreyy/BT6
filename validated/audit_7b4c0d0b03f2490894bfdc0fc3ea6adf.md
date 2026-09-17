### Title
Challenger's per-tick full dispute-game scan and unbounded pending-proof tracking can be flooded by attacker-created dispute games, delaying detection/nullification of fraudulent games - (File: `crates/proof/challenge/src/scanner.rs`, `crates/proof/challenge/src/proof_manager.rs`)

### Summary
This is the closest reachable analog to the Salty.IO whitelisting-queue DOS: a bounded/expensive-to-drain admission structure fed by a single class of proposals can be flooded by anyone able to submit that proposal type, delaying processing of legitimate proposals. In Base, the `DisputeGameFactory.createWithInitData` entry point is callable by any L1 sender ("dispute-game participant"), and every `IN_PROGRESS` game after the anchor is picked up by `GameScanner::scan` on *every* tick and handed to `DisputeProofManager`, which tracks all of them in `pending_proofs` with no evident cap on concurrent proof sessions, only a bound (`MAX_IGNORED_GAMES = 10_000`) on the terminally-ignored set.

### Finding Description
`GameScanner::scan` walks every factory index after the current anchor game on each tick and evaluates it with bounded concurrency (`SCAN_CONCURRENCY = 32`), classifying every `IN_PROGRESS` game into a `GameCategory` [1](#0-0) . There is no cap on the *number* of games scanned per tick — it is "every factory index after the current anchor" rather than a rolling/bounded window [2](#0-1) .

`DisputeProofManager` keeps `pending_proofs: PendingProofs` for every in-flight proof session and only bounds a separate `ignored_games` set (`MAX_IGNORED_GAMES = 10_000`, evicted FIFO) — there is no equivalent bound documented for `pending_proofs` itself [3](#0-2) . Any address able to call `DisputeGameFactory.createWithInitData` (an unprivileged L1 sender) can create arbitrarily many `IN_PROGRESS` games of the tracked `game_type`, each of which the scanner will pick up and the proof manager will begin tracking/requesting proofs for, competing for the same constrained TEE/ZK proving resources used to validate genuinely fraudulent games (e.g., `MAX_CONCURRENT_PROOF_REQUESTS_PER_ENCLAVE = 1` for the Nitro-backed prover pool) [4](#0-3) .

### Impact Explanation
If proof-generation throughput is a scarce, effectively serialized resource (as the `MAX_CONCURRENT_PROOF_REQUESTS_PER_ENCLAVE = 1` constant suggests) and there is no visible admission cap/prioritization distinguishing spam games from a genuinely fraudulent game, an attacker can pad the queue with many benign-but-`IN_PROGRESS` dispute games to crowd out or delay proving/disputing of an actually invalid proposal. If a fraudulent game's dispute window elapses before the challenger reaches and disputes it because of this backlog, the fraudulent output root could resolve, leading to a wrong provable output root and potentially fraudulent withdrawal of funds via `AggregateVerifier`/`DelayedWETH` bond flows — this is a High/Critical-class outcome under the accepted impact list (wrong provable output root, theft of funds).

### Likelihood Explanation
Likelihood is uncertain without confirming (a) the actual bond cost of `createWithInitData` for the challenger's monitored `game_type`, which could make sustained spam economically costly and thus mitigate the analog similar to Salty's staking requirement, and (b) whether `pending_proofs`/proof-request submission actually serializes per game rather than per prover, and whether `driver.rs`/`pending.rs` impose their own concurrency or rate limits that I was unable to inspect due to tool call failures in the final iteration. The scanning side (`GameScanner::scan`) is confirmed unbounded per tick, but I could not verify the full request-submission and retry pipeline (`driver.rs`, `pending.rs`) that would determine whether spam games meaningfully throttle proving throughput of the disputed game versus merely adding cheap RPC-scan overhead.

### Recommendation
- Bound the number of concurrent `pending_proofs` sessions the challenger will maintain, independent of the `MAX_IGNORED_GAMES` ignore-list cap, and prioritize proof requests by dispute-window urgency (time remaining) rather than factory-index order.
- Consider requiring/verifying a sufficiently high bond for the monitored `game_type` in `DisputeGameFactory.initBonds`, and rate-limit or deprioritize scanning of games created by addresses/patterns not already resolved as legitimate proposers, mirroring the "require a percent of stake"/"one proposal per address" mitigation Salty.IO applied.
- Add observability (a metric) for `pending_proofs_len()` and alert operators when it grows disproportionately versus historical baselines, so a flooding attack is visible before dispute windows expire.

### Proof of Concept
Not independently reproducible from the indexed context: reproducing this would require driving `DisputeGameFactory.createWithInitData` from many L1 accounts to create numerous concurrent `IN_PROGRESS` games of the challenger's tracked `game_type`, then verifying via `base_challenger_games_scanned_total`/proof-session metrics whether processing of a separately-injected genuinely fraudulent game is measurably delayed — this would need to be validated end-to-end (e.g., via `crates/infra/challenger-e2e`) which was outside the scope of what I could execute here.

### Citations

**File:** crates/proof/challenge/src/scanner.rs (L121-146)
```rust
/// Scans the `DisputeGameFactory` for dispute games that need validation.
///
/// On every tick the scanner locates the current anchor game in the factory
/// index list, then evaluates every later factory index. This avoids an
/// arbitrary lookback cap while still skipping historical games at or before
/// the accepted anchor.
pub struct GameScanner {
    factory_client: Arc<dyn DisputeGameFactoryClient>,
    verifier_client: Arc<dyn AggregateVerifierClient>,
    anchor_registry_client: Arc<dyn AnchorStateRegistryClient>,
    /// Cached `(anchor_game, factory_index)` for the current anchor game.
    anchor_index: Option<(Address, u64)>,
}

impl std::fmt::Debug for GameScanner {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("GameScanner").finish_non_exhaustive()
    }
}

impl GameScanner {
    /// Maximum number of games to evaluate concurrently during a scan.
    pub const SCAN_CONCURRENCY: usize = 32;

    /// Maximum number of factory indices to inspect in one anchor lookup batch.
    pub const ANCHOR_SEARCH_BATCH_SIZE: u64 = 1024;
```

**File:** crates/proof/challenge/src/scanner.rs (L157-171)
```rust
    /// Scans for candidate games that need validation.
    ///
    /// Every call evaluates every factory index after the current anchor game.
    /// If the registry is still at its starting anchor (`anchorGame == 0`), the
    /// scan starts from index 0. If the anchor game cannot be found in the
    /// factory, the scanner falls back to index 0 rather than risking a missed
    /// game.
    ///
    /// Games are filtered out cheaply via a single `status()` RPC call when
    /// they are no longer `IN_PROGRESS`. Individual game query failures are
    /// logged and skipped so that a transient RPC error on one game does not
    /// abort the entire scan. A later scan retries the whole post-anchor range.
    /// After evaluation, the `base_challenger_games_scanned_total` counter and
    /// `base_challenger_scan_head` gauge are updated.
    pub async fn scan(&mut self) -> Result<Vec<CandidateGame>> {
```

**File:** crates/proof/challenge/src/proof_manager.rs (L31-65)
```rust
pub struct DisputeProofManager<L2: L2Provider, P: ProofRequesterProvider> {
    /// Validates output roots and constructs TEE proof commitments.
    validator: OutputValidator<L2>,
    /// Prover-service requester used to generate and poll fault proofs.
    proof_requester: Arc<P>,
    /// L1 provider used to construct TEE proof requests.
    l1_provider: Arc<dyn L1Provider>,
    verifier_client: Arc<dyn AggregateVerifierClient>,
    /// In-flight proof sessions keyed by game address.
    pending_proofs: PendingProofs,
    ignored_games: HashSet<Address>,
    ignored_game_order: VecDeque<Address>,
    max_proof_duration: Duration,
    tee_submit_retry_limit: u32,
}

impl<L2: L2Provider, P: ProofRequesterProvider> std::fmt::Debug for DisputeProofManager<L2, P> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("DisputeProofManager")
            .field("pending_proofs", &self.pending_proofs.len())
            .field("tee_submit_retry_limit", &self.tee_submit_retry_limit)
            .finish_non_exhaustive()
    }
}

impl<L2: L2Provider, P: ProofRequesterProvider> DisputeProofManager<L2, P> {
    /// Maximum number of times a failed proof job will be retried before being dropped.
    pub const MAX_PROOF_RETRIES: u32 = 3;

    /// Maximum number of terminally ignored games retained to avoid rediscovery churn.
    ///
    /// Evicted games may be rediscovered by a later scan, then re-ignored after
    /// one check.
    pub const MAX_IGNORED_GAMES: usize = 10_000;

```

**File:** crates/proof/tee/nitro-host/src/pool.rs (L17-23)
```rust
/// Maximum number of concurrent proof requests per enclave.
///
/// Proving is CPU- and memory-intensive. The enclave does not reject concurrent
/// requests itself; it would serialize them under load, adding queueing latency
/// and resource pressure. The pool enforces the limit host-side so callers can
/// back off and retry instead of piling up.
pub const MAX_CONCURRENT_PROOF_REQUESTS_PER_ENCLAVE: usize = 1;
```
