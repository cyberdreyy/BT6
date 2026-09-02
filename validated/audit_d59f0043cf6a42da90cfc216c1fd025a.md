### Title
Stale confirmations from removed multisig members remain counted toward the confirmation threshold, allowing a request to execute below the required number of live-member approvals - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only purges confirmations for requests that were *created* by the removed member. Confirmations that the removed member cast on *other* pending requests (created by someone else) are never cleaned up. Because `confirm` counts every string in the `confirmations` set for a request regardless of whether that member is still in `self.members`, a request can reach `num_confirmations` and execute even though one or more of the counted confirmations belong to an account/key that is no longer a member at execution time.

### Finding Description
The multisig invariant is: a request executes only once `num_confirmations` distinct **current members** have confirmed it — i.e. `confirmations_from_live_members == num_confirmations` should be the binding checked before `execute_request` runs.

`confirm()` checks this binding incorrectly: it just compares the *size* of the stored `confirmations` `HashSet<String>` to `num_confirmations`, without re-validating that every entry in that set still corresponds to a member of `self.members`: [1](#0-0) 

The only place confirmations are pruned when a member is removed is `delete_member`, and it filters by request *creator*, not by request *confirmer*: [2](#0-1) 

```rust
let request_ids: Vec<u32> = self
    .requests
    .iter()
    .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
    .collect();
for request_id in request_ids {
    self.confirmations.remove(&request_id);
    self.requests.remove(&request_id);
}
```

`r.member` is the `MultiSigRequestWithSigner.member` field, which is set once, at `add_request` time, to the account that *created* the request: [3](#0-2) 

If member `B` confirms a request created by member `A` (adding `B`'s identity string to that request's `confirmations` set), and `B` is subsequently removed from the multisig via a separate, properly-approved `DeleteMember` request, the cleanup loop in `delete_member` only looks at requests where `r.member == B` (requests B *created*). The confirmation `B` left on `A`'s request is never removed. That stale confirmation continues to count toward `num_confirmations` for `A`'s request even though `B` is no longer a member.

### Impact Explanation
This breaks the K-of-N confirmation invariant that is the entire security guarantee of the multisig contract: `confirm()` can trigger `execute_request()` (which can transfer funds, deploy code, add full-access keys, add/delete members, or make arbitrary function calls on behalf of the account) with fewer than `num_confirmations` confirmations from accounts that are actually members at execution time. This is a "multisig request executed below threshold" scenario, which is explicitly listed as Critical impact: funds can move, contract can be deployed/upgraded, or full-access keys can be added, all with fewer genuine independent approvals than the configured security threshold guarantees.

### Likelihood Explanation
The scenario requires no privileged access beyond normal multisig membership and confirmation actions that are part of ordinary contract usage:
1. Member `A` creates request `R` (e.g., a `Transfer`).
2. Member `B` (a legitimate member at the time) confirms `R`, short of the threshold.
3. Members remove `B` via a normal, correctly-threshold-approved `DeleteMember { member: B }` request — a routine operational action (e.g. offboarding an employee, rotating a compromised key).
4. `B`'s stale confirmation on `R` is never cleared because `delete_member` only clears requests `B` created, not requests `B` merely confirmed.
5. A remaining member confirms `R`, and the count `confirmations.len() + 1 >= num_confirmations` is satisfied by combining live confirmations with `B`'s stale one — the request executes.

This does not require any malicious collusion at the time of removal; it can happen purely through normal member turnover combined with pending requests that were only partially confirmed before a member departs. Any organization that rotates multisig membership while requests are in flight is exposed. Likelihood is moderate-to-high in realistic operational use, though it does require a specific ordering of events (partial confirmation → member removal → completion) rather than a single-transaction exploit.

### Recommendation
When removing a member in `delete_member`, iterate over **all** pending requests (not just ones the member created) and remove the departing member's confirmation string from each request's `confirmations` set. Alternatively (and more robustly), change `confirm()` to recompute the confirmation count only over entries in the stored set that are still present in `self.members` at confirmation time, so stale identities can never contribute toward the threshold regardless of cleanup timing:

```rust
let live_confirmations = confirmations
    .iter()
    .filter(|m| self.members.contains(&MultisigMember::from_string(m)))
    .count();
if live_confirmations as u32 + 1 >= self.num_confirmations { ... }
```

Also apply the equivalent fix to the legacy `multisig/src/lib.rs` (`remove_request`/key-removal flow), which is out of scope for this specific citation set but should be checked for the analogous stale-confirmation issue.

### Proof of Concept
1. Deploy `multisig2` with members `[A, B, C, D]` and `num_confirmations = 3`.
2. `A` calls `add_request(R = Transfer{...})` → `R` created, `confirmations(R) = {}`.
3. `B` calls `confirm(R)` → `confirmations(R) = {B}` (1 < 3, not executed).
4. Separately, members create and fully confirm a `DeleteMember{member: B}` request (using 3 confirmations from `A, C, D`) → executed successfully; `B` removed from `self.members`. In `delete_member`, the cleanup loop finds no requests with `r.member == B` (since `B` never created a request), so `confirmations(R)` is untouched and still contains `B`.
5. `C` calls `confirm(R)` → `confirmations(R) = {B, C}`, size 2, `2 + 1 >= 3` → `remove_request` + `execute_request(R)` fires, transferring funds — even though only `A` (creator, not counted as confirmer) is untouched, and the two actual "confirmations" are from `B` (no longer a member) and `C`. Only one live member (`C`) plus the stale `B` reached the threshold, i.e., the request executed with confirmations from fewer than 3 *current* members. [1](#0-0) [2](#0-1)

### Citations

**File:** multisig2/src/lib.rs (L169-200)
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

**File:** multisig2/src/lib.rs (L356-379)
```rust
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
        match member {
            MultisigMember::AccessKey { public_key } => promise.delete_key(public_key.into()),
            MultisigMember::Account { account_id: _ } => promise,
        }
    }
```
