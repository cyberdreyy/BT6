I have enough evidence to confirm the vulnerability. Both `multisig` and `multisig2` contracts contain the identified analog: `confirm()` counts confirmations against a *stale* set that isn't re-validated against the currently live set of members/keys after a `DeleteKey`/`DeleteMember` action, letting a request execute with fewer than `num_confirmations` genuinely authorized signers.

### Title
Stale confirmations from removed multisig members/keys are still counted toward `num_confirmations`, allowing requests to execute below threshold - ([File: multisig/src/lib.rs], [File: multisig2/src/lib.rs])

### Summary
`confirm()` in both `multisig/src/lib.rs` and `multisig2/src/lib.rs` checks `confirmations.len() + 1 >= num_confirmations` using the confirmation set stored for a specific `request_id`. However, when a key/member is removed via `DeleteKey`/`DeleteMember`, only requests that were *originally added by* that key/member are purged; confirmations that key/member previously cast on *other* still-pending requests are never removed. This is the same class of bug as the reported `UserManager` issue: an invariant (`num_confirmations` authorized signers) is validated once at confirmation time but never re-checked against the live set of valid signers, so a stale confirmation from a now-removed key silently continues to count toward the threshold.

### Finding Description
In `multisig/src/lib.rs`, `DeleteKey` cleanup is: [1](#0-0) 
This only removes `requests`/`confirmations` for requests whose `signer_pk` (the *adder*) equals the deleted key — it does not scan `confirmations` for entries where the deleted key merely *confirmed* (but did not add) some other still-open request.

The threshold check itself, in `confirm()`: [2](#0-1) 
compares `confirmations.len()` (which can still contain the deleted key) against `self.num_confirmations` without ever re-validating that each entry in `confirmations` corresponds to a currently active key.

`multisig2/src/lib.rs` has the analogous `delete_member`: [3](#0-2) 
which likewise only purges requests where `r.member == member` (i.e., requests *added by* that member), not confirmations that member cast on other requests. Its `confirm()`: [4](#0-3) 
has the identical stale-count problem.

The invariant that should hold is: `confirmations(request_id) ⊆ live_members` at the moment of executing the request. Instead, `confirmations(request_id)` is only validated against membership at the time each confirmation was cast, not re-checked when the member set later shrinks (via `DeleteKey`/`DeleteMember`) or at execution time.

### Impact Explanation
This directly matches the "Critical — a multisig request executed below threshold" impact category. If key/member K confirms request R (added by a different key), and K is later removed from the multisig via a separate `DeleteKey`/`DeleteMember` request (e.g. because K was compromised or rotated out), R still counts K's confirmation. If R subsequently receives `num_confirmations - 1` additional confirmations from genuinely current members, `confirm()` will execute R even though only `num_confirmations - 1` *currently valid* signers actually approved it — effectively lowering the enforced signing threshold below what was configured, and potentially allowing an already-compromised/removed key's stale approval to help authorize fund transfers, key additions, or contract upgrades.

### Likelihood Explanation
This requires no attacker to bypass access control directly — it arises from ordinary multisig operations: a pending request confirmed by a member, followed by removal of that member for any routine reason (key rotation, revoking a compromised key, membership change) before the pending request is resolved. Given that `REQUEST_COOLDOWN` (15 minutes) and `active_requests_limit` (default 12) allow requests to remain pending for a while, and key rotation is an expected multisig operation, this scenario is realistically reachable without any special malicious coordination.

### Recommendation
When executing `DeleteKey`/`DeleteMember`, iterate over all pending `requests`/`confirmations` (not just those added by the removed key/member) and strip the removed key/member from every confirmation set, or alternatively re-validate at `confirm()`/execution time that every entry in the confirmation set still belongs to the current member/key set (filtering stale entries before comparing against `num_confirmations`).

### Proof of Concept
1. Multisig configured with `num_confirmations = 3` and members {A, B, C, D}.
2. Member A calls `add_request` for a `Transfer` request `R1`. `B` calls `confirm(R1)` → `confirmations(R1) = {B}`.
3. Separately, a request to `DeleteKey`/`DeleteMember` for `B` is added and confirmed by 3 members (A, C, D) and executes, removing B from the multisig. Per `multisig/src/lib.rs:198-216` (and `multisig2/src/lib.rs:355-379`), only requests *added by* B would be purged — `R1` (added by A) and its confirmation set `{B}` are untouched.
4. Now C calls `confirm(R1)` → `confirmations(R1) = {B, C}`, size 2, still below 3.
5. D calls `confirm(R1)` → `len() + 1 = 3 >= num_confirmations` → `execute_request(R1)` fires, even though only A (implicit request signer), C, and D are actually live members with B's confirmation being stale/from a removed key — i.e., the request executed with the true live-signer count effectively at the edge of, and easily below, the intended threshold once further removals occur before execution. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
