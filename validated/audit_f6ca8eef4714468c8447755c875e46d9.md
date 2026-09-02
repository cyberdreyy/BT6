### Title
Multisig `confirm()` executes requests using stale confirmations from already-removed members, letting a request pass below the live signer threshold - (File: `multisig2/src/lib.rs`)

### Summary
The `MultiSigContract` in `multisig2/src/lib.rs` (and the analogous `multisig/src/lib.rs`) requires `num_confirmations` distinct member confirmations before a request executes. When a member is removed via `DeleteMember`/`DeleteKey`, the contract only deletes *requests originated by* that member; it does not scrub that member's existing confirmations recorded on *other* still-pending requests. Because `confirm()` only counts entries in the `confirmations` set against the current `num_confirmations`, without checking that every confirming identity is still in `self.members`, a stale confirmation from a removed member can be counted toward reaching threshold, allowing a request to execute with fewer live, currently-authorized signers than the configured `num_confirmations`.

### Finding Description
`delete_member` in `multisig2/src/lib.rs` only purges requests where the removed member was the *proposer*: [1](#0-0) 

It never inspects or removes the removed member's public key/account entry from `self.confirmations` maps of *other* pending requests that member had already confirmed. The equivalent v1 contract has the same gap in the `DeleteKey` action handling: [2](#0-1) 

`confirm()` then decides whether to execute purely by comparing the size of the stored confirmation set to `self.num_confirmations`, with no re-validation that each entry in `confirmations` still belongs to `self.members`: [3](#0-2) 

This breaks the intended custody binding: `confirmations counted == live members who confirmed`. Instead the contract enforces `confirmations counted (stale + live) >= num_confirmations`, which can be satisfied with fewer than `num_confirmations` currently-authorized signers.

Concrete sequence:
1. Multisig has members `{A, B, C, D}`, `num_confirmations = 3`.
2. `A` calls `add_request` for `Transfer{amount}` to some receiver → request `R`.
3. `B` calls `confirm(R)` → `confirmations(R) = {A, B}` (2/3).
4. Separately, members execute a `DeleteMember{member: B}` request (via a different, already-fully-confirmed request) — `B` is removed from `self.members` and its access key deleted. `delete_member` does not touch `confirmations(R)`, so `R` still shows `B` as having confirmed.
5. `C` calls `confirm(R)`: `confirmations(R).len() (2) + 1 >= num_confirmations (3)` → true → `execute_request` runs the transfer.

The transfer executes with confirmations from `A`, stale-`B`, and `C` — i.e. only two currently-live members (`A`, `C`) actually authorized it, one fewer than the configured 3-of-4 threshold.

### Impact Explanation
This is a Critical-class issue per the custody rules: "a multisig request executed below threshold." Funds (`Transfer`, `FunctionCall` with deposit, `AddKey`/`AddMember` changes, etc.) can move or the multisig's authorization structure can be modified with fewer live, currently-entitled confirmations than `num_confirmations` mandates, undermining the entire K-of-N security guarantee the contract is meant to provide.

### Likelihood Explanation
This requires no special privilege beyond normal multisig lifecycle activity that already exists in the contract's design: members are removed and added over time (that's an explicit supported action, `DeleteMember`/`DeleteKey`), and requests can remain pending across such membership changes (there is no requirement that all pending requests be resolved before a member is removed). Any organization that rotates or revokes multisig members while a `Transfer`/`FunctionCall` request is still awaiting confirmations is exposed; no attacker deception or out-of-scope privilege is required beyond the multisig's own documented member-management operations.

### Recommendation
When removing a member (`delete_member` / `DeleteKey`), also purge that member's identity from the `confirmations` set of every other pending request (not just requests they proposed). Alternatively, at `confirm()` time, filter/recompute the confirmation count restricted to identities still present in `self.members` before comparing against `num_confirmations`, so stale confirmations from removed members can never count toward execution.

### Proof of Concept
```rust
// members: A, B, C, D ; num_confirmations = 3
let mut c = MultiSigContract::new(members(), 3);

// 1. A proposes a Transfer request R
let request_id = /* as A */ c.add_request(transfer_request.clone());

// 2. B confirms R -> confirmations(R) = {A, B}
/* as B */ c.confirm(request_id);

// 3. Members separately fully-confirm a DeleteMember{B} request,
//    removing B from self.members and deleting its key.
//    delete_member() only purges requests proposed BY B; it never
//    touches confirmations(R), which still contains B's entry.

// 4. C confirms R:
/* as C */ c.confirm(request_id);
// confirmations(R).len() == 2 ({A,B}) ; 2 + 1 >= num_confirmations(3) -> true
// => Transfer executes, authorized only by live members A and C (2/4),
//    not the required 3 live confirmations.
```

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

**File:** multisig2/src/lib.rs (L356-374)
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
