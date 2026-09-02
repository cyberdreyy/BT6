### Title
Stale confirmations from removed multisig members are counted toward the approval threshold, allowing request execution below the live-member quorum - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only purges the requests *created by* the removed member and their `num_requests_pk` entry. It does not scan `self.confirmations` to strip that member's confirmation from *other* pending requests they had previously confirmed. `confirm()` later counts quorum purely by `confirmations.len()`, without checking that every recorded confirmer is still a current member of `self.members`. This lets a request execute with fewer live-member approvals than `num_confirmations`.

### Finding Description
`confirm()` decides execution solely from the size of the stored confirmation set: [1](#0-0) 

`delete_member` removes only the requests originated by the removed member, and cleans up that member's own `num_requests_pk`, but never inspects/updates `self.confirmations` entries belonging to *other* still-pending requests that the removed member had already confirmed: [2](#0-1) 

Concretely:
1. Members A, B, C exist, `num_confirmations = 3`.
2. A creates request `R1` (e.g. `Transfer`). B confirms `R1` → `confirmations[R1] = {B}` (1 confirmation, needs 3).
3. Separately, a `DeleteMember{member: B}` request is confirmed by A and C and executes, removing B from `self.members`. `delete_member` only removes requests whose `r.member == B` (i.e., requests B *created*), not `R1` which B merely confirmed. `confirmations[R1]` still equals `{B}`.
4. Now only A and C are live members. C confirms `R1`: `confirmations.len() + 1 = 2 (B stale) + 1 (C) = ... ` — actually `confirm()` computes `confirmations.len() as u32 + 1 >= num_confirmations`, i.e. `1 + 1 = 2 >= 3` is false in this exact numeric example, but with a larger member set (e.g. 5 members, `num_confirmations = 3`, two members B and D both stale-confirm before removal) the stale confirmations accumulate and only one live confirmer is needed to cross the threshold, executing `R1` (a `Transfer` or other privileged action) with only 1 out of the current live members actually approving it at present.

This breaks the intended custody binding: **confirmations counted == live members who approved**. Instead, `confirmations_counted` can exceed `live_members_who_approved`, because departed members' stale approvals are never invalidated.

### Impact Explanation
This falls under the Critical impact bucket: "a multisig request executed below threshold." An attacker (or any subset of current members colluding with confirmations left behind by since-removed members) can push through a `Transfer`, `AddKey`, `FunctionCall`, or other privileged multisig action without gathering the actual number of currently-authorized approvals — potentially moving NEAR out of the multisig-controlled account, or re-adding attacker-controlled keys, with fewer live signers than the contract's own `num_confirmations` invariant requires.

### Likelihood Explanation
Likelihood is High in any multisig that experiences membership churn (a normal, expected operation exposed via `DeleteMember`). No special privilege is needed beyond what any legitimate member/multisig user already has to create/confirm requests and to eventually remove a member — a completely ordinary workflow. The bug is purely a missing state-cleanup step, not requiring a compromised key, redeploy, or social engineering.

### Recommendation
When deleting a member, iterate over `self.confirmations` for **all** pending requests (not only those created by the removed member) and remove the member's confirmation entry from each set. Alternatively, when tallying confirmations in `confirm()`, filter the stored confirmation set to only those entries still present in `self.members` before comparing against `num_confirmations`.

```diff
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
+        // strip this member's stale confirmation from any other pending request
+        for (request_id, mut confirmations) in self.confirmations.iter() {
+            if confirmations.remove(&member.to_string()) {
+                self.confirmations.insert(&request_id, &confirmations);
+            }
+        }
         // remove num_requests_pk entry for member
         self.num_requests_pk.remove(&member.to_string());
         self.members.remove(&member);
         ...
```

### Proof of Concept
Not fully verified against a running node/test harness in this analysis (no execution environment available). The trace below is derived directly from reading `multisig2/src/lib.rs`:

1. `new(members=[A,B,C,D,E], num_confirmations=3)`.
2. `A.add_request(Transfer{...}) -> request_id = R1`.
3. `B.confirm(R1)` → `confirmations[R1] = {B}`.
4. `D.confirm(R1)` → `confirmations[R1] = {B, D}`.
5. Separately execute a confirmed `DeleteMember{B}` request (requires 3/5 members, e.g. A, C, E) → B removed from `self.members`; `confirmations[R1]` untouched, still `{B, D}`.
6. Execute a confirmed `DeleteMember{D}` request similarly → D removed; `confirmations[R1]` still `{B, D}` (both stale).
7. Now only A, C, E remain as live members (3), and `num_confirmations` is still 3.
8. `C.confirm(R1)`: `confirmations.len() as u32 + 1 = 2 + 1 = 3 >= num_confirmations (3)` → request executes with the `Transfer` action, even though only **one live member (C)** actually approved `R1`; A and E, the other live members, never confirmed it.

This satisfies the rule's Critical criterion "a multisig request executed below threshold," with the binding `confirmations_counted == live_members_approved` broken at step 8: ` [3](#0-2) `.

### Citations

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

**File:** multisig2/src/lib.rs (L355-379)
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
        match member {
            MultisigMember::AccessKey { public_key } => promise.delete_key(public_key.into()),
            MultisigMember::Account { account_id: _ } => promise,
        }
    }
```
