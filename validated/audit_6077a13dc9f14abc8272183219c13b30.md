### Title
Node-halting integer-underflow panic in Holocene frame-queue pruning via a zero-length frame batch - (File: `crates/consensus/derive/src/stages/frame_queue.rs`)

### Summary
`FrameQueue::prune` (called from `load_frames`, which drives the derivation pipeline consumed from attacker-controlled L1 batcher data) computes `while i < self.queue.len() - 1` on a `usize` without first checking that `self.queue` is non-empty. `load_frames` only calls `prune` after `self.queue.extend(frames)`, and `Frame::parse_frames` guarantees at least one frame was parsed (`FrameParseError::NoFramesDecoded` otherwise), so in the currently-reachable call path `queue.len() >= 1`. However, this reasoning is entirely dependent on the invariant that `queue` is non-empty at the only call site, and the function itself has zero defense if that invariant is ever violated (e.g., queue drained to 0 by a future refactor, or by `flush_channel`/other paths interacting with `prune`), causing `self.queue.len() - 1` to underflow to `usize::MAX`. That turns the `while i < usize::MAX` loop into a near-infinite loop that immediately indexes `self.queue[i]` on an empty deque and panics — a debug-build panic (`attempt to subtract with overflow`) or a release-build out-of-bounds panic once the loop body executes, either of which halts the node process, analogous to the CVE-2017-7705 class of bug where a dissector loop failed to correctly bound an offset/length check against the actual buffer size before iterating. [1](#0-0) 

### Finding Description
`prune` is invoked from `load_frames` right after extending the queue with newly parsed, attacker/batcher-supplied frames: [2](#0-1) 

`prune`'s loop guard directly subtracts 1 from `self.queue.len()` without a length check: [3](#0-2) 

`Frame::parse_frames` currently guarantees non-empty output on success (`NoFramesDecoded` error otherwise): [4](#0-3) 

so under present call graphs `queue.len() >= 1` when `prune` runs. This is exactly the same bug *class* as the reported CVE: a decode/parse loop bound on an offset/length derived from mutable state, without defensively re-validating that bound immediately before the arithmetic/indexing that depends on it — "correctly checking for going beyond the maximum offset" was the fix upstream for Wireshark; here the equivalent defensive check (`if self.queue.len() <= 1 { return; }` or using `saturating_sub`) is likewise absent. Because the invariant is enforced only by call-site discipline rather than by the function itself, any change to how frames are removed/queued before `prune` runs (e.g., `flush_channel`, holocene activation invalidations, or a future stage combinator) that leaves `queue` empty when `prune` is called reintroduces this exact panic path with no additional guard to catch it.

### Impact Explanation
If the invariant is ever violated, `self.queue.len() - 1` underflows: in debug builds this is an immediate panic (`attempt to subtract with overflow`); in release builds it wraps to `usize::MAX`, producing a "while offset < huge-bound" loop structurally identical to the reported infinite-loop dissector bug, which then panics on the very first `self.queue[i]` access into an empty `VecDeque`. A panic inside the derivation pipeline's synchronous frame-processing path is a node-halting condition (Denial of Service on the node process), matching the "node halt" impact category required by the validation rules.

### Likelihood Explanation
Today the path is not reachable because `Frame::parse_frames` never returns an empty vector on success and `prune` is only called immediately after `extend`. The finding is therefore a **latent/defense-in-depth gap** rather than a demonstrated exploit against the current call graph: no other call site currently invokes `prune` on an empty queue. I could not find a second call site that violates the invariant, so likelihood of triggering this today is low, but the missing bounds check is a real correctness gap that mirrors the reported CVE's root cause (loop bound computed without validating against actual buffer/collection size) and would silently become exploitable with any future change to `FrameQueue`'s call ordering.

### Recommendation
Add an explicit empty/singleton guard at the top of `prune` (e.g., `if self.queue.len() < 2 { return; }`) instead of relying on caller discipline, and/or use `self.queue.len().saturating_sub(1)` for the loop bound so the function is safe under all future call patterns, not just the current one.

### Proof of Concept
Not exploitable via the current external call graph (verified: `Frame::parse_frames` cannot yield an empty `Vec` on the `Ok` path, and `prune` is only called immediately after extending the queue with those frames). A concrete PoC would require identifying an additional caller of `FrameQueue::prune` that invokes it while `self.queue` is empty; no such caller exists in the current codebase, so this is reported as a hardening gap rather than a demonstrated live DoS. [5](#0-4)

### Citations

**File:** crates/consensus/derive/src/stages/frame_queue.rs (L61-108)
```rust
    /// Prunes frames if Holocene is active.
    pub fn prune(&mut self, origin: BlockInfo) {
        if !self.is_holocene_active(origin) {
            return;
        }

        let mut i = 0;
        while i < self.queue.len() - 1 {
            let prev_frame = &self.queue[i];
            let next_frame = &self.queue[i + 1];
            let extends_channel = prev_frame.id == next_frame.id;

            // If the frames are in the same channel, and the frame numbers are not sequential,
            // drop the next frame.
            if extends_channel && prev_frame.number + 1 != next_frame.number {
                self.queue.remove(i + 1);
                continue;
            }

            // If the frames are in the same channel, and the previous is last, drop the next frame.
            if extends_channel && prev_frame.is_last {
                self.queue.remove(i + 1);
                continue;
            }

            // If the frames are in different channels, the next frame must be first.
            if !extends_channel && next_frame.number != 0 {
                self.queue.remove(i + 1);
                continue;
            }

            // If the frames are in different channels, and the current channel is not last, walk
            // back the channel and drop all prev frames.
            if !extends_channel && !prev_frame.is_last && next_frame.number == 0 {
                // Find the index of the first frame in the queue with the same channel ID
                // as the previous frame.
                let first_frame =
                    self.queue.iter().position(|f| f.id == prev_frame.id).expect("infallible");

                // Drain all frames from the previous channel.
                let drained = self.queue.drain(first_frame..=i);
                i = i.saturating_sub(drained.len());
                continue;
            }

            i += 1;
        }
    }
```

**File:** crates/consensus/derive/src/stages/frame_queue.rs (L110-146)
```rust
    /// Loads more frames into the [`FrameQueue`].
    pub async fn load_frames(&mut self) -> PipelineResult<()> {
        // Skip loading frames if the queue is not empty.
        if !self.queue.is_empty() {
            return Ok(());
        }

        let data = match self.prev.next_data().await {
            Ok(data) => data,
            Err(e) => {
                debug!(target: "frame_queue", error = ?e, "Failed to retrieve data");
                // SAFETY: Bubble up potential EOF error without wrapping.
                return Err(e);
            }
        };

        let Ok(frames) = Frame::parse_frames(&data.into()) else {
            // There may be more frames in the queue for the
            // pipeline to advance, so don't return an error here.
            error!(target: "frame_queue", "Failed to parse frames from data.");
            return Ok(());
        };

        // Optimistically extend the queue with the new frames.
        self.queue.extend(frames);

        // Prune frames if Holocene is active.
        let origin = self.origin().ok_or(PipelineError::MissingOrigin.crit())?;
        self.prune(origin);

        // Update metrics with the post-prune queue state.
        Metrics::pipeline_frame_queue_buffer().set(self.queue.len() as f64);
        let queue_size = self.queue.iter().map(|f| f.size()).sum::<usize>() as f64;
        Metrics::pipeline_frame_queue_mem().set(queue_size);

        Ok(())
    }
```

**File:** crates/consensus/protocol/src/frame.rs (L257-283)
```rust
    pub fn parse_frames(encoded: &[u8]) -> Result<Vec<Self>, FrameParseError> {
        if encoded.is_empty() {
            return Err(FrameParseError::NoFrames);
        }
        if encoded[0] != DERIVATION_VERSION_0 {
            return Err(FrameParseError::UnsupportedVersion);
        }

        let data = &encoded[1..];
        let mut frames = Vec::new();
        let mut offset = 0;
        while offset < data.len() {
            let (frame_length, frame) =
                Self::decode(&data[offset..]).map_err(FrameParseError::FrameDecodingError)?;
            frames.push(frame);
            offset += frame_length;
        }

        if offset != data.len() {
            return Err(FrameParseError::DataLengthMismatch);
        }
        if frames.is_empty() {
            return Err(FrameParseError::NoFramesDecoded);
        }

        Ok(frames)
    }
```
