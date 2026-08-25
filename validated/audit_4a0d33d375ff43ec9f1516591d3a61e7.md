### Title
Unbounded per-address FIFO blocked-task queue in the unified scheduler enables cheap-transaction griefing of legitimate transactions targeting the same account - ([File: unified-scheduler-logic/src/lib.rs])

### Summary
`SchedulingStateMachine` in the unified scheduler queues conflicting transactions per-account in a `VecDeque`-backed FIFO (`UsageQueueInner::Fifo`) that must be drained strictly in arrival order before a later transaction touching the same account can become runnable, mirroring the `KangarooVault`/`LiquidityPool` sequential-queue griefing pattern from the referenced report.

### Finding Description
The unified scheduler's core algorithm is documented as "per-address FIFO queues" where, for a `Capability::FifoQueueing` `UsageQueue`, every conflicting (write- or incompatible read-) request against the same address is pushed onto `blocked_usages_from_tasks: VecDeque<UsageFromTask>` and can only be popped, one at a time, as the address is unlocked by `unlock()` when the currently-holding task is descheduled. [1](#0-0) [2](#0-1) 

Unlocking pops from the front of the FIFO queue and only continues to the next entry once the popped task successfully re-locks and is fully unblocked, i.e. processing is strictly ordered and sequential, exactly like `depositQueue`/`queuedDepositHead` in the vulnerable contracts: [3](#0-2) 

The design doc explicitly acknowledges an unbounded "buffer bloat" risk from a long run of blocked/queued tasks on one address, but states this is deliberately left unhandled by the scheduling logic itself and pushed to a higher layer ("solved elsewhere ... at the scheduler pool"): [4](#0-3) 

Because any ordinary user can submit many cheap, low-value transactions that write-lock a specific shared/writable account (e.g., a popular program state account, a PDA, or any account a victim's legitimate transaction also writes to), those transactions will queue up in the address's FIFO `VecDeque` in arrival order. A legitimate transaction that also needs to write-lock that address is appended to the same queue and cannot be scheduled until every earlier queued transaction is popped and descheduled — analogous to Bob's high-value deposit being stuck behind Alice's 10,000 wei deposits in the reported bug. Since the docstring itself flags this as an accepted, unmitigated design gap ("buffer bloat... should be solved elsewhere"), the specific mitigation depends entirely on whatever bounding is (or isn't) implemented in the calling scheduler pool, which was not confirmed within the available index for this repo/version.

### Impact Explanation
If the higher-level scheduler pool does not itself bound queue depth or provide a way to skip/cancel spam-blocked entries per address, an attacker can cheaply flood a hot writable account with many low-value/self-conflicting transactions, forcing all subsequent legitimate transactions on that same account to wait behind the full backlog before being scheduled/executed within a slot — a compute/ingest-starvation and unfair-ordering griefing vector rather than a direct fund theft, consistent with the Medium severity assigned to the original report (unbounded processing cost/delay imposed on other users, no direct profit for the attacker).

### Likelihood Explanation
Likelihood is moderate: exploiting requires only ordinary transaction submission privileges (no special access), and the FIFO queueing capability and its unbounded `VecDeque` growth are explicitly acknowledged as unmitigated at this layer of the codebase. However, real-world impact depends on account/queue selection at the scheduler-pool layer (e.g., whether `PriorityQueueing` capability, rate limiting, per-account congestion control, or fee-based reordering is applied before transactions reach this state machine), which could not be verified from the code available in this index.

### Recommendation
- Bound the per-address FIFO `blocked_usages_from_tasks` queue depth (or migrate hot/contended addresses to `Capability::PriorityQueueing`, which already exists in this file and reorders by task/fee priority rather than strict arrival order) so that low-fee spam cannot indefinitely delay higher-value legitimate transactions.
- At the scheduler-pool layer, apply per-account congestion accounting or fee-based admission control before a task is registered with a `Fifo` `UsageQueue`, ensuring the "buffer bloat" concern flagged in the module docs is actually mitigated rather than deferred.
- Consider allowing cancellation/eviction of stale low-priority blocked tasks from a `Fifo` queue analogous to adding a "cancel queued deposit" mechanism recommended in the original report.

### Proof of Concept
Conceptual reproduction using the existing test harness pattern in `unified-scheduler-logic/src/lib.rs`:
1. Create one `UsageQueue` with `Capability::FifoQueueing` for a single target `Pubkey` (as in `test_blocked_tasks_writable_2_readonly_then_writable`).
2. Have an attacker-controlled sequence of N low-fee `Task`s created via `SchedulingStateMachine::create_task` that all write-lock the target address, each `schedule_task()`-ed in sequence — every one after the first returns `None` (blocked) and is pushed to the address's `VecDeque`.
3. Append a legitimate high-value `Task` that also write-locks the same address; it is placed at the back of the same FIFO `VecDeque`.
4. Demonstrate that `schedule_next_unblocked_task()` for the legitimate task only becomes available after N `deschedule_task()` calls for every attacker task ahead of it, consistent with the sequential unlock loop: [5](#0-4) 
This reproduces, within the scheduling logic, the same "must process every cheap entry before yours" delay pattern demonstrated by the `testDepositFee` PoC in the original report.

### Citations

**File:** unified-scheduler-logic/src/lib.rs (L90-98)
```rust
//! ### Buffer bloat insignificance
//!
//! The scheduler code itself doesn't care about the buffer bloat problem, which can occur in
//! unified scheduler, where a run of heavily linearized and blocked tasks could be severely
//! hampered by very large number of interleaved runnable tasks along side.  The reason is again
//! for separation of concerns. This is acceptable because the scheduling code itself isn't
//! susceptible to the buffer bloat problem by itself as explained by the description and validated
//! by the mentioned benchmark above. Thus, this should be solved elsewhere, specifically at the
//! scheduler pool.
```

**File:** unified-scheduler-logic/src/lib.rs (L697-729)
```rust
/// Specifically, it holds the current [`Usage`] (or no usage with [`Usage::Unused`]) and which
/// [`Task`]s are blocked to be executed after the current task is notified to be finished via
/// [`::deschedule_task`](`SchedulingStateMachine::deschedule_task`)
#[derive(Debug)]
enum UsageQueueInner {
    Fifo {
        current_usage: Option<FifoUsage>,
        blocked_usages_from_tasks: VecDeque<UsageFromTask>,
    },
    Priority {
        current_usage: Option<PriorityUsage>,
        blocked_usages_from_tasks: PriorityUsageQueue,
    },
}

type UsageFromTask = (RequestedUsage, Task);

impl UsageQueueInner {
    fn with_fifo() -> Self {
        Self::Fifo {
            current_usage: None,
            // Capacity should be configurable to create with large capacity like 1024 inside the
            // (multi-threaded) closures passed to create_task(). In this way, reallocs can be
            // avoided happening in the scheduler thread. Also, this configurability is desired for
            // unified-scheduler-logic's motto: separation of concerns (the pure logic should be
            // sufficiently distanced from any some random knob's constants needed for messy
            // reality for author's personal preference...).
            //
            // Note that large cap should be accompanied with proper scheduler cleaning after use,
            // which should be handled by higher layers (i.e. scheduler pool).
            blocked_usages_from_tasks: VecDeque::with_capacity(128),
        }
    }
```

**File:** unified-scheduler-logic/src/lib.rs (L751-781)
```rust
impl UsageQueueInner {
    fn try_lock(&mut self, new_task: &Task, requested_usage: RequestedUsage) -> LockResult {
        match self {
            Self::Fifo { current_usage, .. } => match current_usage {
                None => Ok(FifoUsage::from(requested_usage)),
                Some(FifoUsage::Readonly(count)) => match requested_usage {
                    RequestedUsage::Readonly => Ok(FifoUsage::Readonly(count.increment())),
                    RequestedUsage::Writable => Err(()),
                },
                Some(FifoUsage::Writable(())) => Err(()),
            }
            .map(|new_usage| {
                *current_usage = Some(new_usage);
            }),
            Self::Priority { current_usage, .. } => match current_usage {
                Some(PriorityUsage::Readonly(tasks)) => match requested_usage {
                    RequestedUsage::Readonly => {
                        assert!(tasks.insert(new_task.task_id(), new_task.clone()).is_none());
                        Ok(())
                    }
                    RequestedUsage::Writable => Err(()),
                },
                Some(PriorityUsage::Writable(_task)) => Err(()),
                None => {
                    *current_usage = Some(PriorityUsage::from(new_task.clone(), requested_usage));

                    Ok(())
                }
            },
        }
    }
```

**File:** unified-scheduler-logic/src/lib.rs (L1224-1248)
```rust
    #[must_use]
    fn try_lock_usage_queues(&mut self, task: Task) -> Option<Task> {
        let mut blocked_usage_count = ShortCounter::zero();

        for context in task.lock_contexts() {
            context.with_usage_queue_mut(&mut self.usage_queue_token, |usage_queue| {
                let lock_result = usage_queue
                    .prepare_lock(&mut self.count_token, &task, context.requested_usage)
                    .and_then(|()| usage_queue.try_lock(&task, context.requested_usage));
                if let Err(()) = lock_result {
                    blocked_usage_count.increment_self();
                    let usage_from_task = (context.requested_usage, task.clone());
                    usage_queue.push_blocked(usage_from_task);
                }
            });
        }

        // no blocked usage count means success
        if blocked_usage_count.is_zero() {
            Some(task)
        } else {
            task.set_blocked_usage_count(&mut self.count_token, blocked_usage_count);
            None
        }
    }
```

**File:** unified-scheduler-logic/src/lib.rs (L1250-1276)
```rust
    fn unlock_usage_queues(&mut self, task: &Task) {
        for context in task.lock_contexts() {
            context.with_usage_queue_mut(&mut self.usage_queue_token, |usage_queue| {
                let mut newly_lockable = usage_queue.unlock(task, context.requested_usage);
                while let Some((lockable_usage, lockable_task)) = newly_lockable {
                    usage_queue
                        .try_lock(&lockable_task, lockable_usage)
                        .unwrap();

                    // When `try_unblock()` returns `None` as a failure of unblocking this time,
                    // this means the task is still blocked by other active task's usages. So,
                    // don't push task into unblocked_task_queue yet. It can be assumed that every
                    // task will eventually succeed to be unblocked, and enter in this condition
                    // clause as long as `SchedulingStateMachine` is used correctly.
                    if let Some(unblocked_task) = lockable_task.try_unblock(&mut self.count_token) {
                        self.unblocked_task_queue.push_back(unblocked_task);
                    }

                    // Try to further schedule blocked task for parallelism in the case of readonly
                    // usages
                    newly_lockable = matches!(lockable_usage, RequestedUsage::Readonly)
                        .then(|| usage_queue.pop_lockable_readonly())
                        .flatten();
                }
            });
        }
    }
```
