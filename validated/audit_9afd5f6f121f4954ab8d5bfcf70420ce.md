## Title
Stale confirmations from deleted multisig members are still counted toward the confirmation threshold, allowing requests to execute below the required K-of-N approval - (File: `multisig2/src/lib.rs`, also present in `multisig/src/lib.rs`)

### Summary
`delete_member` (and the equivalent `DeleteKey` handling in the older `multisig` contract) only purges *requests originated by* the removed member, but never scrubs that member's *confirmations* recorded on other, still-pending requests. Because `confirm()` counts raw confirmation-set size against `num_confirmations` without verifying every counted confirmer is still a current member, a request can later be pushed over the threshold and executed using a confirmation that was cast by an account that is no longer part of the multisig — i.e. the binding "confirmations counted == live members who approved" is broken.

### Finding Description
`delete_member` only cleans up requests where the removed member is the *author* of the request: [1](#0-0) 

It never inspects `self.confirmations` to strip out entries contributed by `member` on requests authored by *someone else*. Those stale entries remain stored: [2](#0-1) 

`confirm()` then decides whether to execute purely by comparing the confirmation-set size to `num_confirmations`, with no check that every account/key already present in the set is still a member: [3](#0-2) 

`assert_valid_request` only validates that the *caller* is currently a member; it never re-validates the pre-existing entries in `self.confirmations`: [4](#0-3) 

Concrete sequence:
1. Members `{A, B, C}`, `num_confirmations = 2`.
2. `B` calls `add_request` creating request `R` (e.g. `Transfer` of contract funds to an attacker-controlled account). `C` calls `confirm(R)` → `confirmations = {C}` (count 1, below threshold, not yet executed).
3. Independently, the multisig legitimately executes a `DeleteMember { member: C }` request (2 confirmations, e.g. `A` and `B`) — a normal, unrelated administrative action (e.g. because `C`'s key was rotated or compromised). `delete_member` only removes requests *authored by* `C`; `R` (authored by `B`) is untouched, and `R`'s confirmation set still contains `C`.
4. `A` now calls `confirm(R)`. `confirmations.len() (=1, still {C}) + 1 >= num_confirmations (=2)` → the request executes.

The `Transfer` (or `AddKey`/`DeployContract`/`FunctionCall`) executes having received only **one** confirmation from a currently live member (`A`); the second "confirmation" belongs to `C`, who is no longer a trusted member of the multisig. The identical structural flaw exists in the older `multisig` contract's `DeleteKey` handling: [5](#0-4) [6](#0-5) 

This is the same class of bug as the reported `BytesUtils.equals` issue: a stored/cached count (`confirmations.len()`) is trusted to represent a live, still-valid set (`current members who approved`), but nothing re-verifies that every element counted still satisfies the membership condition it was originally checked against.

### Impact Explanation
This directly breaks the core custody guarantee of the multisig: "a multisig request executed below threshold." Once at least one prior member has been removed while holding a stale confirmation on a pending request, the number of *live* members required to authorize any subsequent action (including `Transfer` of the account's NEAR balance, `FunctionCall` with a deposit, or `AddKey`/`DeployContract`) drops below the configured `num_confirmations`. In the worst case, repeated member churn can leave many stale confirmations accumulated across pending requests, letting a single current member effectively execute fund transfers alone. This is Critical impact per the rules: a multisig request executed below the intended threshold, resulting in unauthorized movement of NEAR.

### Likelihood Explanation
The trigger conditions are ordinary multisig operations that occur naturally over the life of any multisig account: creating a request, partially confirming it, and later rotating/removing a member (a routine, expected administrative action, e.g., replacing a departing team member or rotating a compromised key) before the original request is confirmed, deleted, or expires. No exploitation of cryptography or external contracts is needed — it is a pure state-management flaw in `delete_member`/`DeleteKey`.

### Recommendation
When removing a member (`delete_member` in `multisig2`, `DeleteKey` handling in `multisig`), iterate over **all** pending requests' confirmation sets (not just requests authored by the removed member) and strip the removed member's entry from each. Alternatively, change `confirm()` to recompute the confirmation count by filtering the stored confirmation set against `self.members` (or valid keys) at confirmation time, rather than trusting the raw stored `HashSet` length.

### Proof of Concept
```
// members = {A, B, C}, num_confirmations = 2

// 1. B creates a Transfer request R (unstaked funds -> attacker account)
let r = c.add_request(MultiSigRequest {
    receiver_id: current_account_id(),
    actions: vec![MultiSigRequestAction::Transfer { amount: BIG_AMOUNT.into() }],
}); // as B

// 2. C confirms R -> confirmations = {C}, count = 1 (below threshold)
c.confirm(r); // as C

// 3. Unrelated, legitimate governance action: remove C from the multisig
//    (requires 2 confirmations from A and B on a separate DeleteMember request)
//    delete_member() only purges requests where r.member == C; R (authored by B) is untouched,
//    and R's confirmations set still contains C.

// 4. A confirms R -> confirmations.len() (1, still {C}) + 1 >= num_confirmations (2)
c.confirm(r); // as A  --> R executes, transferring funds

// Result: R executed with only 1 live member (A) actually approving it at execution time,
// the required 2nd confirmation belongs to C who has already been removed from the multisig.
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

**File:** multisig2/src/lib.rs (L406-421)
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
