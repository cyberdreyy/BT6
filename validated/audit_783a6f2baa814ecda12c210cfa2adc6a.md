### Title
Stale confirmations from removed multisig members can be counted toward the approval threshold, allowing a request to execute below the intended live-member threshold - (File: `multisig/src/lib.rs`, `multisig2/src/lib.rs`)

### Summary
This is not a direct analog to the Salty.IO price-staleness bug (there is no oracle/price-feed pattern in this repository), but it maps to the same bug class the report describes: a value used to authorize a critical action (`_lastPriceOnUpkeepBTC`/`ETH`) is cached and never invalidated when the underlying trust condition changes. In `multisig`/`multisig2`, the analogous cached value is the **set of confirmations already recorded for a pending request** — it is never invalidated when a confirming member/key is subsequently removed from the multisig, breaking the intended binding "confirmations counted == approvals from currently-live members."

### Finding Description
`confirm()` decides whether to execute a request purely by counting entries already stored in `self.confirmations` for that `request_id`: [1](#0-0) 

When a member/key is removed via `DeleteKey` (v1) or `DeleteMember` (v2), the code only purges **requests originated by** that key/member — it filters by `r.signer_pk == pk` (v1) or `r.member == member` (v2), not by whether that key/member appears inside `confirmations` for *other* pending requests: [2](#0-1) [3](#0-2) 

So if member/key `A` confirms a pending request `R` created by `B`, and the multisig later removes `A` (e.g., because `A`'s key was compromised or `A` is being offboarded), `A`'s earlier confirmation entry for `R` is never cleared. `R` still shows `A`'s confirmation as counted. Any subsequent confirmation from a remaining live member can then push `confirmations.len() + 1 >= num_confirmations`, executing `R` with fewer than `num_confirmations` *live* approvals — one of the counted approvals came from a party that has since been stripped of authority.

This exactly matches the custody-binding class called out for this class of report: "confirmations counted versus live members." The root cause is the same as the price-feed bug: an authorization value (`confirmations` set / cached price) is updated on writes but never re-validated/invalidated when the trust condition it depends on (valid key set / fresh price) changes.

### Impact Explanation
This falls under the "Critical" impact bucket explicitly listed in scope: "a multisig request executed below threshold." A transfer, `AddKey`, or `FunctionCall` request can execute with only `num_confirmations - 1` currently-authorized approvers plus one stale approval from a removed/revoked member, defeating the K-of-N security guarantee the multisig is supposed to provide.

### Likelihood Explanation
This requires no attacker-supplied malicious deployment or foundation privilege beyond the normal, documented multisig-membership lifecycle (a member being added/removed is an ordinary operational event, e.g. rotating a compromised key). Any time a pending, unconfirmed/partially-confirmed request exists across a member-removal event, the stale confirmation persists silently, and it takes exactly one additional confirming member to complete execution. The precondition (a partially confirmed pending request outliving a membership change) is realistic given the 15-minute deletion cooldown and multi-step nature of multisig operations.

### Recommendation
- Short term: when removing a key/member (`DeleteKey`/`DeleteMember`), iterate `confirmations` for **all** pending requests (not just those the removed key/member created) and strip that key/member's entry, re-checking whether the remaining confirmation count still meets `num_confirmations`.
- Alternatively/long term: instead of counting cached confirmation entries verbatim, validate at `confirm()`/execution time that every entry in the stored confirmation set still corresponds to a currently valid member/key, discarding stale entries before comparing to `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C}` and `num_confirmations = 2`.
2. `B` calls `add_request` to create a `Transfer` request `R` (0 confirmations, per `add_request` in `multisig2/src/lib.rs:170-200`).
3. `A` calls `confirm(R)` → `confirmations = {A}` (1/2, not yet executed).
4. The group discovers `A`'s key is compromised and submits+confirms a `DeleteMember{A}` request (using B and C's confirmations) — this succeeds and calls `delete_member`, which only clears requests where `r.member == A` (i.e., requests A *created*), per `multisig2/src/lib.rs:355-379`. `R`'s `confirmations = {A}` entry is untouched.
5. `C` calls `confirm(R)` → `confirmations.len() (1) + 1 = 2 >= num_confirmations (2)` → `R` executes, transferring funds, even though `A` is no longer a member and only one currently-live member (`C`) approved it. [4](#0-3)

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
