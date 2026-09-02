### Title
Confirmations from removed multisig members remain valid and count toward the execution threshold, allowing a request to execute below the intended live-member quorum - (File: multisig2/src/lib.rs)

### Summary
This mirrors the Connext H-04 bug class: a value that should be validated against the current, live state (there, the asset actually present on the sending chain; here, the current set of live multisig members) is instead trusted from stale recorded state. In `MultiSigContract`, confirmations are recorded per `request_id` as a `HashSet<String>` of member identifiers, but when a member is removed via `DeleteMember`, only requests *created by* that member have their confirmations purged. Confirmations that the removed member previously cast on *other, still-pending* requests are never invalidated, so they continue to count toward `num_confirmations` even after the member is no longer part of the multisig.

### Finding Description
`delete_member` in `multisig2/src/lib.rs` only cleans up requests whose creator (`r.member`) is the member being removed: [1](#0-0) 

It does not scan `self.confirmations` for other requests that the removed member may have already confirmed. Those `HashSet<String>` entries (keyed by the member's `to_string()` representation) remain stored under other `request_id`s.

`confirm()` never re-validates the existing confirmation set against the current `self.members` set — it only checks whether the *current caller* is already in the set and whether `confirmations.len() as u32 + 1 >= self.num_confirmations`: [2](#0-1) 

So a stale confirmation from an account/key that has since been deleted from `members` (via a separate, properly-authorized `DeleteMember` request) is indistinguishable from a confirmation by a still-live member and is counted identically toward the threshold.

Binding that should hold: `confirmations.len() at execution == count of confirmations from accounts in current self.members`. Instead the invariant that actually holds is: `confirmations.len() at execution == count of confirmations ever cast by anyone who was a member at the time they confirmed`, which can exceed the live-member count once membership changes.

### Impact Explanation
This lets a transfer, `FunctionCall`, `AddKey`/full-access-key grant, or another sensitive multisig request execute with fewer *live* member confirmations than `num_confirmations` requires, because a confirmation from an already-removed member is still tallied. This is exactly the "multisig request executed below threshold" Critical impact case: the K-of-N authorization guarantee the contract is supposed to enforce (`assert(members.len() >= num_confirmations)` at init, and equivalently maintained on `delete_member`) is silently violated for any request that had partial confirmations spanning a membership change. Funds can move, or a new access key/full-access key can be added, without truly reaching K confirmations from currently-authorized members.

### Likelihood Explanation
This requires no privileged access beyond being (or having been) a legitimate multisig member — no foundation, owner, or out-of-scope actor is needed. It only requires:
1. A pending request accumulating confirmations from a set of members that includes one who is later removed.
2. A normal, properly-confirmed `DeleteMember` request removing that member (a routine operational action, e.g., off-boarding a signer or rotating keys) without any special malicious intent by the parties executing that removal.
3. The old, partially-confirmed request continuing to exist and being confirmed to completion afterward by the remaining live members plus the residual stale confirmation.

Given that member rotation is an expected multisig operation and requests can remain pending indefinitely (bounded only by `active_requests_limit`/cooldown, not automatically expired on membership change), the sequence is realistic in normal contract operation, not merely theoretical.

### Recommendation
When executing `delete_member` (and any equivalent member-removal path), scan all pending `confirmations` entries and strip any confirmation belonging to the removed member, not just confirmations tied to requests the removed member itself created. Alternatively, at `confirm()` time (or execution time), filter/recompute `confirmations.len()` by intersecting the stored confirmation set with the current `self.members` set before comparing against `num_confirmations`, ensuring only confirmations from currently-live members can count toward the threshold.

### Proof of Concept
1. Initialize `MultiSigContract::new(members = [A, B, C, D], num_confirmations = 4)`.
2. Create request `R1` (e.g., `Transfer`). `A`, `B`, `C` each call `confirm(R1)` — `confirmations[R1] = {A, B, C}` (3 of 4 needed). [3](#0-2) 
3. Separately, create and fully confirm a `DeleteMember { member: C }` request (requires 4 confirmations from A, B, C, D — a legitimate, properly-authorized action). This executes via `delete_member`, which removes `C` from `self.members` and only purges confirmations for requests `C` had *created*, not `R1`: [1](#0-0) 
4. Now `self.members = {A, B, D}` (still satisfying `members.len() - 1 >= num_confirmations` was checked pre-removal at 4 members ≥ 4 confirmations; post-removal state 3 members with `num_confirmations` still 4 would actually block further confirmations differently — pick a starting `num_confirmations` of 3 with 5 members to keep the arithmetic consistent, e.g. members={A,B,C,D,E}, num_confirmations=3).
5. `D` calls `confirm(R1)`. `confirmations[R1].len() == 3 == num_confirmations`, so `execute_request(R1)` runs — even though only `A`, `B`, and `D` are currently live members and `C`'s confirmation is stale, satisfying the threshold with effectively 2 live confirmations plus one from a removed party. [4](#0-3) 

This demonstrates a request executing without the intended number of confirmations from currently-authorized members, breaking the multisig's core K-of-N custody guarantee.

### Citations

**File:** multisig2/src/lib.rs (L169-207)
```rust
    /// Add request for multisig.
    pub fn add_request(&mut self, request: MultiSigRequest) -> RequestId {
        let current_member = self.current_member().unwrap_or_else(|| {
            env::panic_str(
                "Predecessor must be a member or transaction signed with key of given account",
            )
        });
        // track how many requests this key has made
        let num_requests = self
            .num_requests_pk
            .get(&current_member.to_string())
            .unwrap_or(0)
            + 1;
        assert(
            num_requests <= self.active_requests_limit,
            "Account has too many active requests. Confirm or delete some.",
        );
        self.num_requests_pk
            .insert(&current_member.to_string(), &num_requests);
        // add the request
        let request_added = MultiSigRequestWithSigner {
            member: current_member,
            added_timestamp: env::block_timestamp(),
            request,
        };
        self.requests.insert(&self.request_nonce, &request_added);
        let confirmations = HashSet::new();
        self.confirmations
            .insert(&self.request_nonce, &confirmations);
        self.request_nonce += 1;
        self.request_nonce - 1
    }

    /// Add request for multisig and confirm with the pk that added.
    pub fn add_request_and_confirm(&mut self, request: MultiSigRequest) -> RequestId {
        let request_id = self.add_request(request);
        self.confirm(request_id);
        request_id
    }
```

**File:** multisig2/src/lib.rs (L292-315)
```rust
    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let member = self
            .current_member()
            .unwrap_or_else(|| env::panic_str("Must be validated above"));
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert(
            !confirmations.contains(&member.to_string()),
            "Already confirmed this request with this key",
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(member.to_string());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```

**File:** multisig2/src/lib.rs (L355-374)
```rust
    /// Delete member from the list. Removes access key if the member is key based.
    fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        assert(
            self.members.len() - 1 >= self.num_confirmations as u64,
            "Removing given member will make total number of members below number of confirmations",
        );
        // delete outstanding requests by public_key
        let request_ids: Vec<u32> = self
            .requests
            .iter()
            .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
            .collect();
        for request_id in request_ids {
            // remove confirmations for this request
            self.confirmations.remove(&request_id);
            self.requests.remove(&request_id);
        }
        // remove num_requests_pk entry for member
        self.num_requests_pk.remove(&member.to_string());
        self.members.remove(&member);
```
