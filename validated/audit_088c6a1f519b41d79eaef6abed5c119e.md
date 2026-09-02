## Title
Stale confirmations from deleted multisig members count toward execution threshold - (File: `multisig2/src/lib.rs`, also `multisig/src/lib.rs`)

### Summary
When a multisig member is removed via `DeleteMember` (or `DeleteKey` in the legacy `multisig` contract), the contract only purges confirmations for requests that member **originated**, not confirmations that member **cast on other still-pending requests**. A confirmation given by a member who is later removed remains counted toward `num_confirmations` for any request they previously confirmed but did not originate, letting a request execute with fewer *live* member approvals than the configured threshold requires.

### Finding Description
`confirm()` accumulates approvals in a `HashSet<String>` keyed by member identity and executes the request once `confirmations.len() + 1 >= self.num_confirmations`: [1](#0-0) 

Member removal is handled by `delete_member`, which cleans up requests/confirmations only for requests where the removed member is the **request originator** (`r.member == member`), and separately removes that member's `num_requests_pk` counter and access key: [2](#0-1) 

It never scans other pending requests' `confirmations` sets to strip an entry belonging to the removed member. Since `confirmations` is a plain string set with no link back to `members`, a confirmation cast by member `M` before deletion is indistinguishable at execution time from a confirmation by a currently-valid member. `assert_valid_request`/`current_member()` only validate the *caller* of `confirm`/`add_request`/`delete_request` against the live `members` set — they never re-validate previously stored confirmations: [3](#0-2) 

The equivalent legacy contract has the identical gap: `DeleteKey` only removes requests where `r.signer_pk == pk` (i.e., requests that key originated), leaving that key's confirmations on other pending requests intact: [4](#0-3) [5](#0-4) 

This breaks the intended custody binding: `confirmations counted == live members who approved`. After a member is removed, that equality no longer holds for any request the removed member confirmed but did not originate — a stale, now-invalid confirmation continues to count.

### Impact Explanation
This is a Critical-class impact per the rubric ("a multisig request executed below threshold"). A K-of-N multisig's entire security model rests on requiring K genuinely current signers to approve high-value actions such as `Transfer`, `AddKey`, or `DeployContract`. With this bug, an attacker who is (or was) a member can get a malicious request partially confirmed, and once even one confirming member is later removed from the multisig (for any reason — rotation, compromise response, governance decision), the pending request can be executed with only `K-1` (or fewer) *live* approvals, because the departed member's stale confirmation is still tallied. This effectively lowers the enforced threshold below what the multisig owners configured, and directly enables funds to move (or keys/contract code to be changed) with fewer authorized approvals than required.

### Likelihood Explanation
Requires only normal contract usage: a pending request confirmed by fewer than `num_confirmations` members, followed by a routine `DeleteMember`/`DeleteKey` action removing one of those confirmers (a common operational event — key rotation, offboarding a member, responding to a suspected compromise). No privileged bypass or attacker-controlled deployment is needed beyond the multisig's own documented actions; the flaw is purely in incomplete state cleanup.

### Recommendation
When executing `DeleteMember`/`DeleteKey`, iterate over **all** pending requests' `confirmations` sets (not just requests originated by the removed member) and remove the departing member's entry. Additionally/alternatively, re-validate at execution time (inside `confirm()`, before comparing counts) that every entry in the stored confirmation set still corresponds to a member present in `self.members`, discarding stale entries before checking against `num_confirmations`.

### Proof of Concept
Illustrative sequence (5-member multisig, `num_confirmations = 3`, members A,B,C,D,E):

1. Member A calls `add_request` for `Transfer { amount: X }` to an attacker-controlled account, then `confirm` → confirmations = `{A}` (1/3).
2. Member B calls `confirm(request_id)` → confirmations = `{A, B}` (2/3). Request R not yet executed.
3. Separately, members A, C, D confirm a `DeleteMember { member: B }` request (3/5, legitimate quorum) to remove B from the multisig — this succeeds because `delete_member` only removes requests originated by B; request R, which B merely confirmed, is untouched, and B's entry stays in `confirmations[R]`.
4. Now members are `{A, C, D, E}` (4 live members), yet `confirmations[R]` still contains the stale `B` entry alongside `A`.
5. Member C calls `confirm(request_id)` on R → `confirmations.len() + 1 = 3 >= num_confirmations(3)` → `execute_request` fires the `Transfer` to the attacker.

Only 2 currently-live members (A and C) actually approved this specific transfer, yet it executed as if 3 approvals were present — the stale confirmation from removed member B was silently counted, defeating the K-of-N guarantee. Reference code paths: `confirm`'s threshold check [6](#0-5)  and `delete_member`'s incomplete cleanup [7](#0-6) .

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

**File:** multisig/src/lib.rs (L246-266)
```rust
    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
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
