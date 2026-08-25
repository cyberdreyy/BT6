### Title
Unconditional eviction of a lower-staked peer's connection before the new connection's per-peer-cap check, allowing griefing/DoS against staked connections in the QUIC connection tables - ([File: streamer/src/nonblocking/swqos.rs] / [File: streamer/src/nonblocking/simple_qos.rs])

### Summary
The bug class in the external report — evicting the "minimal" entry from a bounded collection based on the *assumption* that a subsequent insert will succeed, without checking whether that insert actually succeeds — has a structural analog in agave's QUIC staked-connection admission path (`SwQos::try_add_connection` and `SimpleQos::try_add_connection`). Both implementations evict an existing lower-stake connection from `ConnectionTable` via `prune_random` *before* confirming that the new connection can actually be inserted, and the insert can still fail afterward due to the independent per-peer connection cap.

### Finding Description
In `streamer/src/nonblocking/swqos.rs` (`SwQos::try_add_connection`) and the equivalent `streamer/src/nonblocking/simple_qos.rs` (`SimpleQos::try_add_connection`), when a staked peer connects and the staked connection table is full: [1](#0-0) 

```
ConnectionPeerType::Staked(stake) => {
    let mut connection_table_l = self.staked_connection_table.lock().await;

    if connection_table_l.total_size >= self.config.max_staked_connections {
        let num_pruned =
            connection_table_l.prune_random(PRUNE_RANDOM_SAMPLE_SIZE, stake);
        ...
    }

    if connection_table_l.total_size < self.config.max_staked_connections {
        if let Ok((last_update, cancel_connection, stream_counter)) = self
            .cache_new_connection(...)
        { ... return Some(cancel_connection); }
    } else { ... }
}
```

`prune_random` (in `streamer/src/nonblocking/quic.rs`, `ConnectionTable::prune_random`) evicts a randomly sampled connection whose stake is lower than the connecting peer's stake, and permanently reduces `total_size`: [2](#0-1) 

The eviction happens speculatively — the code assumes that, having made room, the subsequent `cache_new_connection` → `ConnectionTable::try_add_connection` will succeed. But `try_add_connection` (in `quic.rs`) independently enforces a **per-peer** connection cap unrelated to the eviction just performed: [3](#0-2) 

```
let connection_entry = self.table.entry(key).or_default();
let has_connection_capacity = connection_entry
    .len()
    .checked_add(1)
    .map(|c| c <= max_connections_per_peer)
    .unwrap_or(false);
if has_connection_capacity {
    ...
    self.total_size += 1;
    Some(...)
} else {
    if let Some(connection) = connection {
        connection.close(...);
    }
    None
}
```

If the connecting peer already holds `max_connections_per_peer` entries for its own key (e.g., it opened several concurrent QUIC connections under the same pubkey/IP), this check fails and the function returns `None`. In that case, `cache_new_connection` returns `Err`, and the outer `try_add_connection` falls through without ever re-inserting the connection that was just evicted from a *different* peer via `prune_random`. The eviction is not reverted; `total_size` remains decremented, and the victim's connection has been permanently and irreversibly closed for no compensating benefit. This exactly mirrors the reported bug class: an operation that consumes/removes the "minimal" member of a bounded set based on an unchecked assumption that a subsequent add will succeed.

### Impact Explanation
A staked peer (any validator/staker, not just a "minimal" one) can repeatedly open QUIC connections beyond its own `max_connections_per_peer` cap while the global staked connection table is at capacity. Each such attempt triggers `prune_random`, which evicts another (lower-staked) validator's already-established connection, yet the attacker's own connection is rejected by the per-peer cap and never occupies the freed slot. Repeating this is a low-cost way to progressively evict other staked peers' TPU/QUIC connections, degrading their ability to submit transactions — a DoS/griefing vector on the fee/transaction-ingest path, gated only by holding some non-trivial stake (higher than the victims sampled).

### Likelihood Explanation
The path is reachable by any external QUIC client presenting a valid staked identity (an ordinary validator's TPU QUIC connection), requiring no special privilege beyond controlling a keypair with stake above the target's. Opening more than `max_connections_per_peer` concurrent connections is trivial for a network client. The only precondition is that the staked connection table is near/at `max_staked_connections`, which is a normal steady-state condition on a busy cluster.

### Recommendation
In `SwQos::try_add_connection` / `SimpleQos::try_add_connection`, only perform the `prune_random` eviction transactionally with the insertion — e.g., check the connecting peer's own per-peer capacity *before* pruning another entry, or make `ConnectionTable::try_add_connection` and the preceding `prune_random` atomic under a single reservation so that a failed insert restores/does not consume the evicted slot. At minimum, verify the connecting peer's `has_connection_capacity` prior to invoking `prune_random`, mirroring the recommended fix of checking `AddressSet.add`'s return value before relying on the state change it implies.

### Proof of Concept
Conceptual reproduction (based on code paths above; not executed in a live cluster):
1. Attacker controls staked keypair A with stake `S_A`. Cluster's staked connection table is at `max_staked_connections`.
2. Attacker already has `max_connections_per_peer` live connections open under key A.
3. Attacker opens one more QUIC connection under key A. Server calls `try_add_connection`: since `total_size >= max_staked_connections`, `prune_random` runs and evicts a randomly sampled connection belonging to some victim validator B with stake `S_B < S_A`.
4. Server then calls `cache_new_connection` → `ConnectionTable::try_add_connection`, which fails because attacker A already has `max_connections_per_peer` entries under key A; returns `None`.
5. Net effect: victim B's connection is permanently closed, `total_size` decremented, and attacker gained nothing — repeatable at will to continuously evict other staked peers' connections.

Note: I was unable to fully verify the exact default values of `max_staked_connections`/`max_connections_per_peer` used in production configuration within the indexed content, and did not find explicit unit tests covering this specific interleaving (eviction followed by a per-peer-cap insert failure), so the exhaustion magnitude in a real deployment is unconfirmed but the code-level logic gap is directly demonstrated above.

### Citations

**File:** streamer/src/nonblocking/swqos.rs (L355-375)
```rust
                ConnectionPeerType::Staked(stake) => {
                    let mut connection_table_l = self.staked_connection_table.lock().await;

                    if connection_table_l.total_size >= self.config.max_staked_connections {
                        let num_pruned =
                            connection_table_l.prune_random(PRUNE_RANDOM_SAMPLE_SIZE, stake);
                        self.stats
                            .num_evictions_staked
                            .fetch_add(num_pruned, Ordering::Relaxed);
                        update_open_connections_stat(&self.stats, &connection_table_l);
                    }

                    if connection_table_l.total_size < self.config.max_staked_connections {
                        if let Ok((last_update, cancel_connection, stream_counter)) = self
                            .cache_new_connection(
                                client_connection_tracker,
                                connection,
                                connection_table_l,
                                conn_context,
                            )
                        {
```

**File:** streamer/src/nonblocking/quic.rs (L986-1006)
```rust
    pub(crate) fn prune_random(&mut self, sample_size: usize, threshold_stake: u64) -> usize {
        let num_pruned = std::iter::once(self.table.len())
            .filter(|&size| size > 0)
            .flat_map(|size| {
                let mut rng = rng();
                repeat_with(move || rng.random_range(0..size))
            })
            .map(|index| {
                let connection = self.table[index].first();
                let stake = connection.map(|connection: &ConnectionEntry<S>| connection.stake());
                (index, stake)
            })
            .take(sample_size)
            .min_by_key(|&(_, stake)| stake)
            .filter(|&(_, stake)| stake < Some(threshold_stake))
            .and_then(|(index, _)| self.table.swap_remove_index(index))
            .map(|(_, connections)| connections.len())
            .unwrap_or_default();
        self.total_size = self.total_size.saturating_sub(num_pruned);
        num_pruned
    }
```

**File:** streamer/src/nonblocking/quic.rs (L1008-1051)
```rust
    pub(crate) fn try_add_connection<F: FnOnce() -> Arc<S>>(
        &mut self,
        key: ConnectionTableKey,
        port: u16,
        client_connection_tracker: ClientConnectionTracker,
        connection: Option<Connection>,
        peer_type: ConnectionPeerType,
        last_update: Arc<AtomicU64>,
        max_connections_per_peer: usize,
        stream_counter_factory: F,
    ) -> Option<(Arc<AtomicU64>, CancellationToken, Arc<S>)> {
        let connection_entry = self.table.entry(key).or_default();
        let has_connection_capacity = connection_entry
            .len()
            .checked_add(1)
            .map(|c| c <= max_connections_per_peer)
            .unwrap_or(false);
        if has_connection_capacity {
            let cancel = self.cancel.child_token();
            let stream_counter = connection_entry
                .first()
                .map(|entry| entry.stream_counter.clone())
                .unwrap_or_else(stream_counter_factory);
            connection_entry.push(ConnectionEntry::new(
                cancel.clone(),
                peer_type,
                last_update.clone(),
                port,
                client_connection_tracker,
                connection,
                stream_counter.clone(),
            ));
            self.total_size += 1;
            Some((last_update, cancel, stream_counter))
        } else {
            if let Some(connection) = connection {
                connection.close(
                    CONNECTION_CLOSE_CODE_TOO_MANY.into(),
                    CONNECTION_CLOSE_REASON_TOO_MANY,
                );
            }
            None
        }
    }
```
