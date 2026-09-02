This confirms the vulnerable pattern. In both `multisig/src/lib.rs` and `multisig2/src/lib.rs`, when a member/key is removed (`DeleteKey` / `delete_member`), only requests **originated by** that member are purged; confirmations that member previously cast on **other still-pending requests** are never removed from those requests' confirmation sets.

## Finding

**Binding broken:** `confirmations.len() >= num_confirmations` is meant to equal "number of *live* members who approved," but stale confirmations from removed members remain counted, so `confirmed_count(recorded) > confirmed_count(live)`.

In `multisig/src/lib.rs`:
- `confirm()` at [1](#0-0)  only checks `confirmations.len() as u32 + 1 >= self.num_confirmations` — it never re-validates that each public key in the `confirmations` set is still a currently-authorized key on the account.
- `DeleteKey` handling in `execute_request()` at [2](#0-1)  only deletes requests where `r.signer_pk == pk` (i.e., requests *added* by the removed key) — it does not scan `self.confirmations` for other pending requests that the removed key had confirmed, so those stale confirmations survive.

The same pattern exists in `multisig2/src/lib.rs`:
- `confirm()` at [3](#0-2)  has the identical counting logic.
- `delete_member()` at [4](#0-3)  only purges requests where `r.member == member` (requests originated by the removed member), leaving that member's confirmations on other pending requests untouched.

### Scenario
1. Members A, B, C, D exist; `num_confirmations = 3`.
2. Someone creates request X (e.g. transfer funds). A and B confirm it (2/3 confirmations recorded, one short of threshold).
3. Before a third confirmation, the group separately votes to remove member B (e.g., B is compromised or off-boarded) via a `DeleteKey`/`DeleteMember` request — this request is unrelated to X and does not touch X's confirmations.
4. B is now removed from `members` / no longer has an access key, but the `confirmations` map for request X still contains B's key/id.
5. C confirms request X. `confirmations.len() + 1 = 3 >= num_confirmations (3)` — the contract executes X using a confirmation from a member (B) who is no longer live, effectively executing with only 2 real live confirmations out of the required 3.

This mirrors the reported LivenessGuard analog: the "confirmations counted" set diverges from "confirmations by currently live members," letting a request execute below the actual live-member threshold — a Critical-class issue per the rules ("a multisig request executed below threshold"). [2](#0-1) [4](#0-3) 

### Title
Stale confirmations from removed multisig members are still counted toward the execution threshold - (File: multisig/src/lib.rs, multisig2/src/lib.rs)

### Summary
`confirm()` in both `multisig/src/lib.rs` and `multisig2/src/lib.rs` counts confirmations purely by the size of the `confirmations` `HashSet` for a request, and `DeleteKey`/`delete_member` only remove requests *originated* by the removed key/member, never confirmations that key/member cast on *other* pending requests. A member removed after confirming a request remains counted, allowing a request to execute with fewer live confirmations than `num_confirmations` requires.

### Finding Description
`assert_valid_request()` at [5](#0-4)  validates that the *caller* is a current member, but the threshold check in `confirm()` ( [6](#0-5) ) never re-validates the *previously recorded* confirmers against the current `members` set. Once a member is removed via `delete_member()` ( [4](#0-3) ), only requests they authored are cleaned up; their entries inside other requests' `confirmations` sets persist indefinitely. The same holds for the original `multisig` contract's `DeleteKey` path ( [2](#0-1) ), which filters by `signer_pk` (the request author) rather than iterating `self.confirmations` to strip the removed key from every pending request.

### Impact Explanation
This breaks the intended equality `count(confirmations recorded) == count(confirmations from currently live members)`. A request can be executed by promise (transferring funds, adding/deleting keys, deploying code, etc. via `execute_request()`) once the stale + fresh confirmation count reaches `num_confirmations`, even though fewer *live* members actually approved it. This is a multisig request executed below its real threshold — Critical impact per the security policy.

### Likelihood Explanation
Requires normal, unprivileged multisig operation: any member removal (a routine key-rotation/offboarding action) that happens to overlap with an outstanding unconfirmed request is sufficient — no attacker privilege escalation or malicious deployment needed, only ordinary sequencing of two legitimate multisig operations.

### Recommendation
When removing a key/member (`DeleteKey` in `multisig/src/lib.rs`, `delete_member` in `multisig2/src/lib.rs`), iterate over all entries in `self.confirmations` and strip the removed key/member from every set, not just delete requests the removed key authored. Alternatively, re-validate at `confirm()` time that every entry in the stored `confirmations` set for a request still corresponds to a current member/key before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with members A, B, C, D and `num_confirmations = 3`.
2. A calls `add_request_and_confirm` for request X (e.g. `Transfer`) — confirmations = {A}.
3. B calls `confirm(X)` — confirmations = {A, B}, still below threshold, request stays pending.
4. Separately, the group executes a request removing B via `DeleteMember` (self-request), which calls `delete_member()` [4](#0-3)  — B is removed from `members`, but request X's `confirmations` entry ({A, B}) is untouched since X's `r.member` (A) is not B.
5. C calls `confirm(X)` — `confirmations.len() + 1 = 3 >= num_confirmations(3)` at [7](#0-6) , so `execute_request(request)` runs and transfers funds, even though only A and C are still live members (2 live confirmations, not 3).

### Citations

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

**File:** multisig/src/lib.rs (L248-266)
```rust
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert!(
            !confirmations.contains(&env::signer_account_pk()),
            "Already confirmed this request with this key"
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(env::signer_account_pk());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
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

**File:** multisig2/src/lib.rs (L406-423)
```rust
    /// Prevents access to calling requests and make sure request_id is valid - used in delete and confirm
    fn assert_valid_request(&mut self, request_id: RequestId) {
        // request must come from key added to contract account
        assert(
            self.current_member().is_some(),
            "Caller (predecessor or signer) is not a member of this multisig",
        );
        // request must exist
        assert(
            self.requests.get(&request_id).is_some(),
            "No such request: either wrong number or already confirmed",
        );
        // request must have
        assert(
            self.confirmations.get(&request_id).is_some(),
            "Internal error: confirmations mismatch requests",
        );
    }
```
