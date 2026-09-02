Confirmed the same pattern exists in both `multisig/src/lib.rs` (`DeleteKey` action at lines 198-216) and `multisig2/src/lib.rs` (`delete_member` at lines 355-379): when a key/member is removed, only requests that key/member *originated* are cleaned up — confirmations that key/member *cast on other still-pending requests* are never purged from the `confirmations` map, so a stale confirmation from a now-removed signer remains counted toward the threshold in `confirm()`.

### Title
Stale confirmations from removed multisig members/keys still count toward execution threshold, allowing execution below live-member quorum - (File: `multisig2/src/lib.rs`, `multisig/src/lib.rs`)

### Summary
`confirm()` in both `multisig/src/lib.rs` and `multisig2/src/lib.rs` compares `confirmations.len() as u32 + 1 >= self.num_confirmations` to decide whether to execute a pending request. `delete_member` (multisig2) and the `DeleteKey` action (multisig) only strip confirmations/requests that the removed member *created*, not confirmations that member *cast on other pending requests*. A confirmation recorded by a member who is later removed therefore remains in the `confirmations` set and is still counted toward the K-of-N threshold, letting a request execute with fewer live, currently-authorized confirmers than `num_confirmations` requires.

### Finding Description
In `multisig2/src/lib.rs`:
- `confirm()` [1](#0-0)  increments and checks the confirmation count stored in `self.confirmations: LookupMap<RequestId, HashSet<String>>` without re-validating that every entry in the set still belongs to a current member of `self.members`.
- `delete_member()` [2](#0-1)  only deletes requests where `r.member == member` (requests *authored* by the removed member) and removes that member from `self.members` and `num_requests_pk`. It never scans `self.confirmations` for entries where the removed member appears as a *confirmer* of some other pending request.

The same pattern exists in `multisig/src/lib.rs`'s `DeleteKey` action [3](#0-2) , which purges requests filtered by `r.signer_pk == pk` (author) and `num_requests_pk`, but never touches `self.confirmations` entries where `pk` is present as a confirming key on a *different* request.

Consequently, once a member/key confirms request R (but R hasn't reached threshold yet) and is later removed from the multisig via a separate, legitimately-executed `DeleteMember`/`DeleteKey` request, R's stored confirmation set still contains that now-invalid identity. When the remaining live members later confirm R, the stale entry is counted in `confirmations.len()`, letting `confirm()` reach `num_confirmations` and execute R using fewer genuinely live confirmations than the threshold mandates.

**Binding broken:** `confirmations counted (self.confirmations.len())` should equal `confirmations from members ∈ self.members (live)`, but after a member removal this invariant breaks — a ghost confirmation from a removed member is counted as if it were live.

### Impact Explanation
This allows a multisig request (e.g., `Transfer`, `AddKey`/`AddMember`, `DeployContract`, `FunctionCall`) to execute with fewer live confirming members than the configured K-of-N threshold — matching the Critical impact category "a multisig request executed below threshold." Funds or privileged operations controlled by the multisig account can move or execute without the intended quorum of currently-authorized signers.

### Likelihood Explanation
This requires only normal, expected multisig lifecycle operations — no external hacking, no privilege beyond being one of the N members: (1) a request is created and partially confirmed, (2) one of its confirmers is later removed from the multisig (an ordinary membership-rotation event that itself is executed properly through the multisig's own threshold), and (3) the stale, still-pending request is later pushed over threshold by remaining live confirmations. Member rotation is a normal, expected occurrence in a long-lived multisig, making this readily reachable without any additional attacker privilege.

### Recommendation
When removing a member/key (`delete_member` in `multisig2/src/lib.rs`, `DeleteKey` action in `multisig/src/lib.rs`), iterate over `self.confirmations` for all pending requests and remove the departing member's/key's entry from each confirmation set, or re-validate at `confirm()` time that every entry in the stored confirmation set still corresponds to a current `self.members` entry before counting it toward the threshold.

### Proof of Concept
Using `multisig2` with members `{A, B, C, D}` and `num_confirmations = 3`:
1. `A` calls `add_request(R)` (e.g., `Transfer` to an attacker-controlled account). `confirmations[R] = {}`.
2. `B` calls `confirm(R)` → `confirmations[R] = {B}` (1 < 3, not executed).
3. `C` calls `confirm(R)` → `confirmations[R] = {B, C}` (2 < 3, not executed).
4. Separately, the multisig legitimately executes a `DeleteMember{C}` request (using confirmations from `A`, `B`, `D` on that unrelated request) — `delete_member` removes `C` from `self.members`, but since `C` did not *author* `R`, `confirmations[R]` is untouched and still contains `C`.
5. `A` calls `confirm(R)` → `confirmations[R].len() + 1 = 2 + 1 = 3 >= num_confirmations (3)` → `execute_request(R)` fires, transferring funds, even though only `A` and `B` (2 live members) actually authorized it — `C`'s confirmation is a ghost from a removed member. [4](#0-3) [5](#0-4)

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
