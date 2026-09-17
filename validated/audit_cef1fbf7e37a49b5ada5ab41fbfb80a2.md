### Title
Unbounded per-tick dispute-game scan lets a griefer inflate the challenger's post-anchor scan set, delaying detection of a fraudulent game past its timeout - ([File: crates/proof/challenge/src/scanner.rs])

### Summary
`GameScanner::scan()` re-evaluates *every* `DisputeGameFactory` index between the current anchor and `game_count` on each tick, with no cap on how large that range may grow [1](#0-0) . The comment on the struct explicitly states this design choice: "this avoids an arbitrary lookback cap while still skipping historical games at or before the accepted anchor" [2](#0-1) . Because game creation via `DisputeGameFactory` is permissionless (any account can call `createWithInitData`) [3](#0-2) , a dispute-game participant can grow this scanned range without bound simply by creating many `IN_PROGRESS` games, exactly mirroring the JOJO bug class where a user opens unbounded positions that a downstream unbounded loop (`getTotalExposure`) must later traverse.

### Finding Description
Each `scan()` call computes `scan_start` as one past the anchor game's factory index and then evaluates the full range `scan_start..game_count` [4](#0-3) . The anchor only advances when a game is resolved and accepted by the `AnchorStateRegistry`; games that remain `IN_PROGRESS` (including maliciously created ones designed never to resolve, or resolved slowly by design) stay inside the scanned range on every subsequent tick. Since game creation is an ordinary, permissionless, bonded transaction available to any dispute-game participant, an attacker can call `createWithInitData` (or the higher-level factory function) repeatedly to keep pushing `game_count` upward while never advancing the anchor, causing the post-anchor working set the scanner must evaluate every tick to grow monotonically and without limit — the exact "malicious user inflates an iterated collection so a legitimate downstream operation can no longer complete in bounded resources" pattern from the JOJO report, where lots of self-created positions blow up the gas cost of a victim's later `getTotalExposure` loop.

The scan does bound *concurrency* (`SCAN_CONCURRENCY = 32`) [5](#0-4) , but it does not bound the *total* number of indices visited per tick, so the wall-clock time and RPC volume of `scan()` scale linearly with the number of undecided games sitting after the anchor.

### Impact Explanation
If the scan set grows large enough that a single scan tick regularly exceeds the polling interval, the driver's scan-validate-prove-submit loop (`ChallengeDriver::step`, which calls `scanner.scan()` every tick) falls behind real time [6](#0-5) . A genuinely fraudulent game (an invalid TEE or ZK output-root proposal) submitted concurrently with the griefing games can then sit unvalidated until its `expectedResolution` timeout elapses and it resolves in the proposer's favor with a **wrong provable output root** finalized on L1 — the strongest impact category listed in scope (wrong provable output root / permanent freezing or theft of funds tied to the bad root). This is a direct availability attack on the fault-proof program's ability to challenge in time, analogous to the JOJO liquidation-blocking DoS.

### Likelihood Explanation
Creating dispute games is a normal, permissionless action gated only by the type's init bond [7](#0-6) ; no privileged role or contract compromise is required. An attacker willing to pay bonds for many games (which are eventually recoverable for correctly-resolved games, or forfeited only for invalid ones) can sustain a large `IN_PROGRESS` backlog indefinitely, and the scanner code comment shows this was a known, accepted design tradeoff ("avoids an arbitrary lookback cap") rather than an oversight caught by a bound.

### Recommendation
Bound the number of factory indices evaluated per scan tick (an explicit lookback/window cap, paginated across ticks) instead of always scanning the entire post-anchor range in one pass, and/or track per-tick scan progress so a large backlog is drained incrementally without starving newly created legitimate games of timely validation. Consider also rate-limiting or bonding growth of the unresolved `IN_PROGRESS` set so griefing games cannot indefinitely inflate the working set the challenger must revisit every tick.

### Proof of Concept
1. Attacker repeatedly calls `DisputeGameFactory.createWithInitData` (or the higher-level create call), each time paying `initBonds(gameType)`, to create N `IN_PROGRESS` games that never resolve (e.g., games whose proofs are deliberately withheld or delayed near their `expectedResolution`).
2. Because the anchor only advances on resolution, all N games remain in `scan_start..game_count` on every `GameScanner::scan()` tick [4](#0-3) .
3. As N grows, `scan()`'s per-tick RPC/CPU cost grows linearly, eventually causing `ChallengeDriver::step` ticks to exceed the desired cadence.
4. Attacker (or a colluding proposer) submits one genuinely invalid TEE/ZK output-root proposal alongside the noise games; the challenger's delayed scan fails to validate/nullify it before `expectedResolution`, and the invalid game resolves, finalizing a wrong output root.

### Citations

**File:** crates/proof/challenge/src/scanner.rs (L121-147)
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

**File:** crates/proof/challenge/src/scanner.rs (L171-190)
```rust
    pub async fn scan(&mut self) -> Result<Vec<CandidateGame>> {
        let game_count = self.factory_client.game_count().await?;

        if game_count == 0 {
            debug!("factory has no games");
            return Ok(vec![]);
        }

        let end = game_count - 1;
        let scan_start = self.scan_start_index(game_count).await;

        let games_to_scan = game_count.saturating_sub(scan_start);

        let scanner = &*self;
        let results: Vec<(u64, Result<Option<CandidateGame>>)> =
            stream::iter(scan_start..game_count)
                .map(|i| async move { (i, scanner.evaluate_game(i).await) })
                .buffer_unordered(Self::SCAN_CONCURRENCY)
                .collect()
                .await;
```

**File:** crates/proof/contracts/src/dispute_game_factory.rs (L19-26)
```rust
        /// Creates a new dispute game with proof data passed to `initializeWithInitData`.
        function createWithInitData(
            uint32 gameType,
            bytes32 rootClaim,
            bytes calldata extraData,
            bytes calldata initData
        ) external payable returns (address proxy);

```

**File:** crates/proof/contracts/src/dispute_game_factory.rs (L37-38)
```rust
        /// Returns the bond required to create a game of the given type.
        function initBonds(uint32 gameType) external view returns (uint256);
```

**File:** crates/proof/challenge/src/driver.rs (L135-156)
```rust
    /// Executes a single scan-validate-prove-submit cycle.
    pub async fn step(&mut self) -> eyre::Result<()> {
        self.proof_manager.poll_pending_proofs(&self.submitter).await;
        if let Some(bond_manager) = &mut self.bond_manager
            && let Err(e) =
                bond_manager.discover_claimable_games(&*self.verifier_client, &self.submitter).await
        {
            warn!(error = %e, "bond discovery scan failed");
        }
        self.anchor_updater.poll(&*self.verifier_client, &self.submitter).await;

        let candidates = self.scanner.scan().await?;

        for candidate in candidates {
            let index = candidate.index;
            if let Err(e) = self.process_candidate(candidate).await {
                warn!(error = %e, game_index = index, "failed to process candidate");
            }
        }

        Ok(())
    }
```
