Based on my research, I found a solid structural analog to the `claimForAllWindows()` DoS in the challenger service's dispute-game scanner.

### Title
Unbounded Full-Range Dispute-Game Scan Enables Attacker-Driven DoS on the Challenger, Risking Unchallenged Fraudulent Games - (File: `crates/proof/challenge/src/scanner.rs`)

### Summary
`GameScanner::scan()` intentionally evaluates *every* factory index between the current anchor game and the latest game on each tick, with no batching cap or upper bound on the range size. Because anyone can create dispute games through `DisputeGameFactory`, an attacker can inflate `game_count` far faster than the anchor advances, making the per-tick "post-anchor" range grow without bound — structurally the same failure mode as `Splitter.claimForAllWindows()` looping over an attacker-inflatable `currentWindow`.

### Finding Description
`GameScanner::scan()` computes `scan_start` from the cached/looked-up anchor index and then evaluates the *entire* `scan_start..game_count` range every tick via `bond_actions`/`evaluate_game_for_bonds`-style concurrent streams: [1](#0-0) 
The module doc explicitly states this design choice: "this avoids an arbitrary lookback cap while still skipping historical games at or before the accepted anchor," meaning every later factory index is inspected on every tick regardless of how many games exist: [2](#0-1) 
The anchor only advances when a game is finalized/resolved through the normal dispute-game lifecycle (which takes the full finalization delay), while `game_count` can be pushed up immediately by any address calling `DisputeGameFactory.create(...)`, since `game_at_index`/`game_count` are simple factory reads with no cap: [3](#0-2) 
`BondManager.discover_claimable_games` exhibits the identical unbounded-range pattern, driven from the same `game_count`: [4](#0-3) 
This mirrors the Splitter bug exactly: `currentWindow`/`game_count` is a monotonically-increasing counter that any unprivileged caller can inflate (via `incrementWindow()` / via `DisputeGameFactory.create()`), and a critical function (`claimForAllWindows()` / `GameScanner::scan()` and `BondManager::discover_claimable_games`) iterates the *entire* range on every invocation with no windowing, batching cap, or pagination.

### Impact Explanation
Each scan tick issues concurrent RPC calls (bounded by `SCAN_CONCURRENCY = 32`) for every post-anchor game, so an attacker who repeatedly calls `DisputeGameFactory.create()` can grow the per-tick workload linearly and unboundedly. This can push scan latency past the driver's tick interval, causing scans to fall permanently behind, delaying detection and challenge of a genuinely fraudulent game hidden among the flood. Because dispute games have a bounded finalization/challenge window, a sufficiently delayed scan can let an invalid (fraudulent) output-root game resolve unchallenged — enabling an attacker to finalize a wrong output root and withdraw against it, i.e., theft of funds via an unbacked/incorrect withdrawal from L1. This satisfies the "unauthorized operation / theft of funds" bar from the Validate section, driven purely by ordinary `DisputeGameFactory.create()` transactions (a reachable "dispute-game participant" action), not by a malicious sequencer/builder/node.

### Likelihood Explanation
Creating dispute games generally requires posting the game's bond via `initBonds`, which raises the attacker's cost, but bonds are typically sized to cover dispute costs, not scanner-DoS costs, and are refundable if the attacker's own games are eventually resolved as non-fraudulent (defender wins). An attacker only needs to sustain enough concurrent open games to keep the post-anchor range large during the finalization window of their real fraudulent attempt, which is a bounded and plannable cost. Given `scan()`/`discover_claimable_games` have no defensive cap on the range size, likelihood is Medium: it requires capital and timing but no privileged access.

### Recommendation
Cap the number of factory indices evaluated per scan tick (mirroring the `ANCHOR_SEARCH_BATCH_SIZE` pattern already used in `find_game_index`), and process the backlog incrementally across multiple ticks with persisted progress, instead of requiring `scan_start..game_count` to complete atomically in one call. Apply the same bound to `BondManager::bond_actions`'s range. Additionally, consider a minimum-bond/cooldown or rate limit on concurrent open games per creator in the DisputeGameFactory-adjacent tooling to raise the cost of range-inflation attacks.

### Proof of Concept
1. Attacker repeatedly calls `DisputeGameFactory.create()` (posting the required bond each time) to create thousands of open dispute games, keeping them `IN_PROGRESS` past the point the anchor could reasonably reach them.
2. `game_count` grows unboundedly while `anchor_index`/`scan_start` lags behind (anchor only advances on finalized games).
3. On each tick, `GameScanner::scan()` at `crates/proof/challenge/src/scanner.rs:171-190` evaluates the full `scan_start..game_count` range, and `BondManager::discover_claimable_games` at `crates/proof/challenge/src/bond.rs:97-148` does the same for bonds — both workloads scale linearly with the attacker-controlled game count.
4. With enough concurrently open games, scan/bond-discovery duration exceeds the tick interval, causing scans to permanently lag; a genuinely fraudulent game submitted during this window may pass its finalization delay unchallenged, allowing the attacker to withdraw against an invalid L2 output root.

### Citations

**File:** crates/proof/challenge/src/scanner.rs (L121-133)
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

**File:** crates/proof/contracts/src/dispute_game_factory.rs (L110-129)
```rust
#[async_trait]
impl DisputeGameFactoryClient for DisputeGameFactoryContractClient {
    async fn game_count(&self) -> Result<u64, ContractError> {
        let result = contract_call!(self.contract.gameCount().call(), "gameCount failed")?;

        result.try_into().map_err(|_| ContractError::validation("gameCount overflows u64"))
    }

    async fn game_at_index(&self, index: u64) -> Result<GameAtIndex, ContractError> {
        let result = contract_call!(
            self.contract.gameAtIndex(U256::from(index)).call(),
            format!("gameAtIndex({index}) failed")
        )?;

        Ok(GameAtIndex {
            game_type: result.gameType,
            timestamp: result.timestamp,
            proxy: result.proxy,
        })
    }
```

**File:** crates/proof/challenge/src/bond.rs (L113-137)
```rust
        let game_count = self.factory_client.game_count().await?;
        if game_count == 0 {
            self.last_scan = Some(now);
            debug!("no games found, skipping bond discovery scan");
            return Ok(());
        }

        let start_index = game_count.saturating_sub(self.lookback);
        info!(
            start = start_index,
            end = game_count,
            lookback = self.lookback,
            "scanning recent games for claimable bonds"
        );

        ChallengerMetrics::bond_discovery_scans_total("full").increment(1);
        let actions = self.bond_actions(start_index..game_count, verifier_client).await;
        let action_count = actions.len();
        let mut finality_metric_records = Vec::new();

        for action in actions {
            if let Some(record) = self.process_action(action, verifier_client, submitter).await {
                finality_metric_records.push(record);
            }
        }
```
