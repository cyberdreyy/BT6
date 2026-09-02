Both `multisig/src/lib.rs` (`DeleteKey` action) and `multisig2/src/lib.rs` (`delete_member`) share the same flaw: when a key/member is removed, the contract only purges **requests originated by that key/member** and clears its `num_requests_pk` entry — it never scans other still-pending requests' `confirmations` sets to strip out a vote **cast by** that key/member before removal.### Title
Stale confirmations from a removed multisig key/member still count toward the approval threshold, allowing requests to execute below the live quorum - (File: `multisig/src/lib.rs`, `multisig2/src/lib.rs`)

### Summary
When a key (`DeleteKey`, in `multisig`) or member (`DeleteMember`, in `multisig2`) is removed from the multisig, the contract only purges the requests that were *originated* by that key/member. It never scans the `confirmations` map of *other still-pending* requests to strip a vote that the removed key/member had already cast on them. As a result, a confirmation previously cast by an account/key that is no longer a member still counts toward `num_confirmations` when a subsequent `confirm()` call is evaluated, letting a request execute even though fewer than `num_confirmations` *current* members actually approved it.

### Finding Description
`confirm()` in both contracts determines execution purely by counting entries in the `confirmations` `HashSet` for a request and comparing against `self.num_confirmations`: [1](#0-0) 

This binding should be: `confirmations.len() == number of distinct *current* members who approved`. But `confirmations` is a raw historical set of member identifiers, never re-validated against current membership at read time.

`delete_member()` / the `DeleteKey` action only cleans up:
- requests where the deleted member/key is the *originator* (`r.member == member` / `r.signer_pk == pk`), and
- the `num_requests_pk` counter for that member/key. [2](#0-1) [3](#0-2) 

It does **not** iterate over `self.confirmations` to remove the deleted member's/key's vote from *other* pending requests they had merely confirmed (not originated). `current_member()` / signature checks in `confirm()` and `assert_valid_request()` only gate whether the *caller* is presently a member; they never re-validate the *existing* entries already stored in a request's `confirmations` set.

Concretely: `members = {A, B, C, D}`, `num_confirmations = 3`.
1. A submits request `R1` (e.g. `Transfer` of contract funds) and auto-confirms → `confirmations(R1) = {A}`.
2. B confirms → `confirmations(R1) = {A, B}` (2/3, not yet executable).
3. Separately, a `DeleteMember{B}` request reaches quorum (via A, C, D) and executes, removing B from `members` and revoking B's access key. This step does not touch `R1`'s confirmations, since `R1.member == A`, not `B`.
4. `members` is now `{A, C, D}`. D calls `confirm(R1)`. `assert_valid_request` and `current_member()` succeed because D is a current member. `confirmations.len() (2, containing A and the now-removed B) + 1 (D) = 3 >= num_confirmations (3)` → `R1` executes.

`R1` executed with only two genuinely-current-member approvals (A and D) plus one phantom vote from an already-removed member (B), i.e., below the real live-member threshold of 3.

### Impact Explanation
This breaks the core multisig custody guarantee that any privileged action (transferring funds, deploying/upgrading code, adding keys, changing `num_confirmations`) requires `K` genuinely current members to agree. An attacker who is one of the surviving members (or colludes with one) can pre-stage confirmations before a co-conspirator is removed, then finish the quorum later with fewer live approvers than intended — enabling an under-threshold execution of `Transfer`, `AddKey`, `FunctionCall`, `DeployContract`, etc. This matches the "multisig request executed below threshold" Critical impact category, since funds or privileged operations can move without the number of *current* approvers actually reaching the configured `K`.

### Likelihood Explanation
Medium: it requires normal, legitimate multisig operation flow (some member submits/confirms a request, then later another legitimate `DeleteMember`/`DeleteKey` request executes, removing a member who had already confirmed a still-pending, unrelated request) — no key compromise, foundation action, or redeploy is required. Membership churn (onboarding/offboarding signers) combined with any lingering unconfirmed request is a realistic operational scenario, especially since `active_requests_limit` allows up to 12 concurrently open requests per member (a long window during which a member removal can leave stale votes).

### Recommendation
When executing `DeleteMember` (`multisig2`) or `DeleteKey` (`multisig`), iterate over **all** entries in `self.confirmations` (not just requests originated by the removed member/key) and remove the departing member's/key's identifier from every confirmation set, re-persisting the updated set. Alternatively, validate at `confirm()`-time that every identifier already present in `confirmations` still belongs to `self.members` before counting it toward `num_confirmations`.

### Proof of Concept
Using `multisig2/src/lib.rs`:
1. Initialize with `members = [A, B, C, D]`, `num_confirmations = 3`.
2. As A: `add_request_and_confirm(Transfer{amount, receiver_id: A})` → `request_id = R1`, `confirmations(R1) = {A}`.
3. As B: `confirm(R1)` → `confirmations(R1) = {A, B}` (2/3).
4. Separately obtain quorum (A, C, D) to `add_request_and_confirm` / `confirm` a `DeleteMember{member: B}` request targeting the multisig's own account — this executes and removes B from `members`, deletes B's access key, but does not touch `confirmations(R1)`.
5. As D: `confirm(R1)`. `current_member()` succeeds (D is a member). `confirmations(R1).len() (2) + 1 = 3 >= num_confirmations (3)` → `execute_request` runs the `Transfer`, sending funds to A, even though the request was truly ratified only by A and D (a stale, phantom vote from removed member B made up the difference). [1](#0-0) [2](#0-1)

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
