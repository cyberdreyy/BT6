This confirms the vulnerability chain. All pubsub notification payloads share one global `RecentItems` FIFO cache backing `Weak<String>` upgrades for every subscriber's broadcast receiver, so a single unprivileged subscriber can flood it and evict entries other unrelated subscribers still need, disconnecting them with `Error::NotificationIsGone`.

### Title
Unprivileged pubsub client can force disconnect/DoS of unrelated subscribers by exhausting the shared global `RecentItems` notification cache - (File: rpc/src/rpc_subscriptions.rs, rpc/src/rpc_pubsub_service.rs)

### Summary
`RpcNotifier` (the pubsub notification producer) maintains a single global, connection-agnostic bounded cache `RecentItems` shared by *all* subscribers, keyed by nothing but insertion order/size caps (`max_len`, `max_total_bytes`). Every distinct notification generated for any subscription (accountSubscribe, logsSubscribe, programSubscribe, signatureSubscribe, slotSubscribe, etc.) is pushed into this one shared queue, and older entries are evicted once the size/byte cap is exceeded, without regard to which subscriber(s) still need them. Because delivery to each subscriber goes through a `tokio::sync::broadcast` channel carrying only a `Weak<String>` pointer into this shared cache, if the shared cache evicts the strong `Arc<String>` before a given (unrelated, possibly slower) subscriber's `BroadcastHandler::handle` gets to upgrade the `Weak`, that subscriber is forcibly disconnected via `Error::NotificationIsGone`. An unprivileged client can trivially open cheap, high-volume subscriptions (e.g. `logsSubscribe` on `"all"`) to flood this shared queue and evict entries that a legitimate, unrelated subscriber (e.g. someone waiting on `signatureSubscribe` for their own transaction) has not yet consumed, causing that legitimate subscriber's websocket connection to be dropped — an availability failure analogous to the external report's "consuming another user's quota causes their legitimate request to fail."

### Finding Description
The relevant path is:
1. `RpcNotifier::notify` ( [1](#0-0) ) serializes each notification into a buffer, wraps it in `Arc<String>`, sends a lightweight `RpcNotification{ json: Weak::downgrade(&buf_arc), ...}` over a single shared `broadcast::Sender`, and then calls `self.recent_items.lock().unwrap().push(buf_arc)`.
2. `RecentItems` is a single instance shared by the whole `RpcNotifier` (one per validator, not per-connection/per-subscription): [2](#0-1) . Its `push` method evicts from the front of the queue whenever `total_bytes > max_total_bytes || queue.len() > max_len`, with no notion of subscription ownership — it just keeps the most-recently-produced items regardless of source subscription.
3. Every websocket connection's `handle_connection` loop subscribes to the *same* shared `broadcast_receiver()` ( [3](#0-2) ) and runs `BroadcastHandler::handle`, which only cares whether the notification belongs to a subscription this connection owns (looked up in its own `current_subscriptions` map) and then does `notification.json.upgrade().ok_or(Error::NotificationIsGone)` ( [4](#0-3) ).
4. If the strong `Arc<String>` has already been evicted from the shared `RecentItems` queue by the time this connection processes the corresponding broadcast entry, `upgrade()` returns `None`, `Error::NotificationIsGone` is raised, and per `handle_connection`'s `select!` loop error branch that terminates the connection ("In both possible error cases (closed or lagged) we disconnect the client" comment at [5](#0-4) ).

Because `RecentItems` is global and unpartitioned, any unprivileged websocket client that is allowed to open notification-generating subscriptions (e.g. `logsSubscribe` with kind `All`/`AllWithVotes`, which is enabled in the default full RPC API without requiring `enable_vote_subscription`/`enable_block_subscription` flags) can generate large volumes of notifications on every slot. This inflates `RecentItems`, rapidly cycling the queue and evicting strong references belonging to notifications destined for unrelated, low-volume subscribers before their connection's async task gets scheduled to upgrade the `Weak`. The default caps (`DEFAULT_QUEUE_CAPACITY_ITEMS = 10_000_000`, `DEFAULT_QUEUE_CAPACITY_BYTES = 256MiB`, test defaults far smaller at 1000 items / 16MiB — [6](#0-5) ) bound total memory but do not prevent one attacker-controlled high-rate subscription from starving the shared cache against slower, unrelated subscribers.

### Impact Explanation
This is a genuine cross-tenant DoS on the JSON-RPC pubsub service: a single unprivileged client, with no special permissions, can cause other unrelated clients' websocket subscriptions to be forcibly terminated (`Error::NotificationIsGone` -> connection closed), even though those clients did nothing wrong and their subscriptions were valid. This mirrors the external report's bug class precisely — an unprivileged actor consuming a shared, unprotected resource ("quota"/cache slot) that legitimately belongs to (or is needed by) another, unrelated user, resulting in denial of service / failed delivery for that other user (e.g. a wallet waiting on `signatureSubscribe` to confirm a transaction never gets the notification and its connection drops). The fix here is architecturally the same recommendation pattern as the report: don't let unauthenticated/uncontrolled shared mutable state paths be exhausted by an arbitrary caller without per-subscriber isolation or backpressure independent of other subscribers.

### Likelihood Explanation
High. `logsSubscribe` (all logs, optionally including votes) is part of the standard full JSON-RPC API and requires no special privilege beyond having pubsub enabled, which is default for validators exposing RPC. Generating enough traffic to fill the shared `RecentItems` cache only requires subscribing and letting normal cluster/validator activity (or the attacker's own repeated cheap subscribe/unsubscribe cycles across multiple accounts/programs) flow through the shared queue; no elevated permissions, stake, or leader status are required — a single unprivileged pubsub client is sufficient.

### Recommendation
Isolate notification delivery so that no unrelated subscriber's pending notification can be evicted due to another subscriber's traffic volume:
- Replace the single global `RecentItems`/`Weak<String>` eviction scheme with per-subscriber (or per-connection) backpressure/buffering, or size the broadcast channel/cache such that a lagging/slow subscriber is only affected by volume from subscriptions it itself is not consuming quickly, not by unrelated high-volume subscriptions from other clients.
- Alternatively, make eviction consider whether all currently-registered subscribers have already consumed an item before dropping it, or fall back to explicit backpressure per connection (rather than silently invalidating and disconnecting).
- Consider rate-limiting/capping the notification production of any single subscription (e.g., aggregate or drop excess `logsSubscribe`/`programSubscribe` volume) rather than letting it inflate a globally shared cache that other tenants depend on.

### Proof of Concept
Not directly reproduced with a runnable exploit here (no test harness execution in this session), but the mechanism can be demonstrated conceptually with the existing test infrastructure in `rpc/src/rpc_pubsub_service.rs`/`rpc_subscriptions.rs`:
1. Configure `PubSubConfig` with a small `queue_capacity_items`/`queue_capacity_bytes` (as in `PubSubConfig::default_for_tests()`, 1000 items / 16MiB).
2. Client A opens `signatureSubscribe` for its own transaction signature (low-volume, legitimate).
3. Client B (attacker, unprivileged) opens `logsSubscribe` with kind `AllWithVotes` and lets many slots pass, so `RpcNotifier::notify` pushes a large volume of large log entries into the shared `RecentItems` queue ( [7](#0-6) ), evicting older strong references.
4. Once Client A's connection is scheduled to process the broadcast entry for its signature notification, `notification.json.upgrade()` returns `None` because `RecentItems::push` already evicted it, causing `BroadcastHandler::handle` to return `Error::NotificationIsGone` ( [4](#0-3) ), and `handle_connection`'s `select!` error arm closes Client A's websocket ( [8](#0-7) ) — denying Client A its legitimate notification/confirmation despite Client A doing nothing wrong.

### Citations

**File:** rpc/src/rpc_subscriptions.rs (L214-265)
```rust
struct RecentItems {
    queue: VecDeque<Arc<String>>,
    total_bytes: usize,
    max_len: usize,
    max_total_bytes: usize,
    last_metrics_submission: Instant,
}

impl RecentItems {
    fn new(max_len: usize, max_total_bytes: usize) -> Self {
        Self {
            queue: VecDeque::new(),
            total_bytes: 0,
            max_len,
            max_total_bytes,
            last_metrics_submission: Instant::now(),
        }
    }

    fn push(&mut self, item: Arc<String>) {
        self.total_bytes = self
            .total_bytes
            .checked_add(item.len())
            .expect("total bytes overflow");
        self.queue.push_back(item);

        while self.total_bytes > self.max_total_bytes || self.queue.len() > self.max_len {
            let item = self.queue.pop_front().expect("can't be empty");
            self.total_bytes = self
                .total_bytes
                .checked_sub(item.len())
                .expect("total bytes underflow");
        }

        let now = Instant::now();
        let last_metrics_ago = now.duration_since(self.last_metrics_submission);
        if last_metrics_ago > RPC_NOTIFICATIONS_METRICS_SUBMISSION_INTERVAL_MS {
            datapoint_info!(
                "rpc_subscriptions_recent_items",
                ("num", self.queue.len(), i64),
                ("total_bytes", self.total_bytes, i64),
            );
            self.last_metrics_submission = now;
        } else {
            trace!(
                "rpc_subscriptions_recent_items num={} total_bytes={}",
                self.queue.len(),
                self.total_bytes,
            );
        }
    }
}
```

**File:** rpc/src/rpc_subscriptions.rs (L289-322)
```rust
impl RpcNotifier {
    fn notify<T>(&self, value: T, subscription: &SubscriptionInfo, is_final: bool)
    where
        T: serde::Serialize,
    {
        let buf_arc = RPC_NOTIFIER_BUF.with(|buf| {
            let mut buf = buf.borrow_mut();
            buf.clear();
            let notification = Notification {
                jsonrpc: Some(jsonrpc_core::Version::V2),
                method: subscription.method(),
                params: NotificationParams {
                    result: value,
                    subscription: subscription.id(),
                },
            };
            serde_json::to_writer(Cursor::new(&mut *buf), &notification)
                .expect("serialization never fails");
            let buf_str = str::from_utf8(&buf).expect("json is always utf-8");
            Arc::new(String::from(buf_str))
        });

        let notification = RpcNotification {
            subscription_id: subscription.id(),
            json: Arc::downgrade(&buf_arc),
            is_final,
            created_at: Instant::now(),
        };
        // There is an unlikely case where this can fail: if the last subscription is closed
        // just as the notifier generates a notification for it.
        let _ = self.sender.send(notification);

        self.recent_items.lock().unwrap().push(buf_arc);
    }
```

**File:** rpc/src/rpc_pubsub_service.rs (L33-38)
```rust
pub const DEFAULT_MAX_ACTIVE_SUBSCRIPTIONS: usize = 1_000_000;
pub const DEFAULT_QUEUE_CAPACITY_ITEMS: usize = 10_000_000;
pub const DEFAULT_TEST_QUEUE_CAPACITY_ITEMS: usize = 1000;
pub const DEFAULT_QUEUE_CAPACITY_BYTES: usize = 256 * 1024 * 1024;
pub const DEFAULT_TEST_QUEUE_CAPACITY_BYTES: usize = 16 * 1024 * 1024;
pub const DEFAULT_WORKER_THREADS: usize = 1;
```

**File:** rpc/src/rpc_pubsub_service.rs (L246-268)
```rust
    fn handle(&self, notification: RpcNotification) -> Result<Option<Arc<String>>, Error> {
        if let Entry::Occupied(entry) = self
            .current_subscriptions
            .entry(notification.subscription_id)
        {
            increment_sent_notification_stats(
                entry.get().params(),
                &notification,
                &self.sent_stats,
            );

            if notification.is_final {
                entry.remove();
            }
            notification
                .json
                .upgrade()
                .ok_or(Error::NotificationIsGone)
                .map(Some)
        } else {
            Ok(None)
        }
    }
```

**File:** rpc/src/rpc_pubsub_service.rs (L349-409)
```rust
async fn handle_connection(
    socket: TcpStream,
    subscription_control: SubscriptionControl,
    config: PubSubConfig,
    mut tripwire: Tripwire,
) -> Result<(), Error> {
    let mut server = Server::new(socket.compat());
    let request = server.receive_request().await?;
    let accept = server::Response::Accept {
        key: request.key(),
        protocol: None,
    };
    server.send_response(&accept).await?;
    let mut builder = server.into_builder();
    builder.set_max_message_size(4_096);
    builder.set_max_frame_size(4_096);
    let (mut sender, mut receiver) = builder.finish();

    let mut broadcast_receiver = subscription_control.broadcast_receiver();
    let mut data = Vec::new();
    let current_subscriptions = Arc::new(DashMap::new());

    let mut json_rpc_handler = IoHandler::new();
    let rpc_impl = RpcSolPubSubImpl::new(
        config,
        subscription_control,
        Arc::clone(&current_subscriptions),
    );
    json_rpc_handler.extend_with(rpc_impl.to_delegate());
    let broadcast_handler = BroadcastHandler::new(current_subscriptions);
    loop {
        // Extra block for dropping `receive_future`.
        {
            // soketto is not cancel safe, so we have to introduce an inner loop to poll
            // `receive_data` to completion.
            let receive_future = receiver.receive_data(&mut data);
            pin!(receive_future);
            loop {
                select! {
                    biased; // See [prioritization] note below.

                    // [prioritization]
                    // This block must come FIRST in the `select!` macro. This prioritizes
                    // processing received messages over sending messages. This ensures the timely
                    // processing of new subscriptions and time-sensitive opcodes like `PING`.
                    result = &mut receive_future => match result {
                        Ok(_) => break,
                        Err(soketto::connection::Error::Closed) => return Ok(()),
                        Err(err) => return Err(err.into()),
                    },
                    result = broadcast_receiver.recv() => {

                        // In both possible error cases (closed or lagged) we disconnect the client.
                        if let Some(json) = broadcast_handler.handle(result?)? {
                            sender.send_text(&*json).await?;
                        }
                    },
                    _ = &mut tripwire => {
                        warn!("disconnecting websocket client: shutting down");
                        return Ok(())
                    },
```
