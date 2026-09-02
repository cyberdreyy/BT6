### Title
Stale confirmations from removed multisig members can push a request past threshold with fewer live approvers - (File: `multisig2/src/lib.rs`, `multisig/src/lib.rs`)

### Summary
`delete_member` (v2) / the `DeleteKey` action (v1) only purge outstanding **requests that were created by** the removed member, but they never scan and strip that member's **confirmations cast on other members' requests**. Since `confirm()` counts confirmations purely by set size against `num_confirmations`, a stale confirmation left behind by a since-removed member still counts toward the approval threshold, letting a request execute while fewer live members than `num_confirmations` actually approved it.

### Finding Description
In `multisig2/src/lib.rs`, `confirm()` decides whether to execute a request purely by comparing the size of the stored `confirmations: HashSet<String>` for that request to `self.num_confirmations`: [1](#0-0) 

Membership removal is handled by `delete_member`, which only deletes requests where `r.member == member` (i.e., requests *originated* by the removed member) along with their confirmations. It does not touch `self.confirmations` entries for requests originated by *other* members that the removed member had already confirmed: [2](#0-1) 

The equivalent v1 code exhibits the same pattern: `DeleteKey` removes only requests whose `signer_pk` matches the deleted key, and their confirmations, but never removes that key's confirmation entries from confirmation sets belonging to requests created by other keys: [3](#0-2) 

Custody-relevant equality that should hold: `confirmations counted at execution time == confirmations from currently-live members`. The bug breaks this: `confirmations counted >= live-member confirmations`, since a departed member's stale approval remains in the set.

Concrete flow (v2, 3 members A/B/C, `num_confirmations = 2`):
1. Member B calls `add_request` (not `add_request_and_confirm`) with a `Transfer` request to some receiver. No confirmation is recorded yet.
2. Member A (e.g., compromised, or about to be legitimately removed for unrelated reasons) calls `confirm(request_id)`. Since `confirmations.len()+1 (=1) < num_confirmations (=2)`, this just inserts A into the confirmations set and returns — the transfer is *not yet* executed.
3. The multisig (via a separate, properly-authorized self-request executed with 2 live confirmations) removes member A with `DeleteMember`. `delete_member` only deletes requests where `r.member == A` — B's transfer request is untouched, and A's already-recorded confirmation on it survives in `self.confirmations`.
4. Now only B and C remain as members, with `num_confirmations` still 2. Member C calls `confirm(request_id)`. `confirmations.len()+1 (=2) >= num_confirmations (=2)` → the request executes.

Result: the `Transfer` executed with only **one** currently-live member (C) actually approving it at confirmation time, while the K-of-N threshold (2 live members) was never truly met — A's stale, now-invalid confirmation was reused to reach the threshold.

### Impact Explanation
This directly matches the in-scope Critical impact category "a multisig request executed below threshold." Any request type (`Transfer`, `FunctionCall`, `AddKey`, `DeployContract`, etc.) can be smuggled through this way as long as one confirmer is later removed before the threshold is reached, then a single remaining live member finishes it off. In the worst case this allows NEAR to be moved, or the account's key set/contract code to be altered, without the intended number of currently-trusted members having approved.

### Likelihood Explanation
No privileged role is required beyond being a normal (even soon-to-be-removed) member — a realistic scenario is exactly the case where a member is removed *because* they are suspected compromised or malicious: any confirmation they cast before removal remains valid. The precondition (member removal happening while there are outstanding, partially-confirmed requests they touched) is a normal operational occurrence for any active multisig, not a contrived edge case, so this is straightforward to trigger without redeploys or social engineering.

### Recommendation
When removing a member/key, iterate all outstanding requests' confirmation sets (not just those the member originated) and strip the removed member's identifier from each. Alternatively, re-validate at `confirm()`/execution time that every entry in the stored confirmation set still corresponds to a current member before counting it toward `num_confirmations`.

### Proof of Concept
Using `multisig2`:
1. Deploy with `members = [A, B, C]`, `num_confirmations = 2`.
2. `B.add_request(transfer_request)` → `request_id`.
3. `A.confirm(request_id)` → confirmations = `{A}`, size 1 < 2, no execution.
4. Separately, execute a `DeleteMember { member: A }` self-request with legitimate 2/2 confirmations from B and C (independent of step 2-3's pending request) — see `delete_member` at [4](#0-3) , which does not touch `request_id`'s confirmations since `r.member == B`, not `A`.
5. `C.confirm(request_id)` → `confirmations.len()+1 == 2 >= num_confirmations(2)` at [5](#0-4)  → `execute_request` runs the transfer, even though only C is a currently live member who confirmed; A's stale confirmation counted toward the threshold after A ceased to be a member.

### Citations

**File:** multisig2/src/lib.rs (L294-315)
```rust
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

**File:** multisig/src/lib.rs (L198-216)
```rust
                MultiSigRequestAction::DeleteKey { public_key } => {
                    self.assert_self_request(receiver_id.clone());
                    let pk: PublicKey = public_key.into();
                    // delete outstanding requests by public_key
                    let request_ids: Vec<u32> = self
                        .requests
                        .iter()
                        .filter(|(_k, r)| r.signer_pk == pk)
                        .map(|(k, _r)| k)
                        .collect();
                    for request_id in request_ids {
                        // remove confirmations for this request
                        self.confirmations.remove(&request_id);
                        self.requests.remove(&request_id);
                    }
                    // remove num_requests_pk entry for public_key
                    self.num_requests_pk.remove(&pk);
                    promise.delete_key(pk)
                }
```
