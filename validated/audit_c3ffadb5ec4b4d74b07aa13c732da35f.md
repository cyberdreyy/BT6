### Title
Stale confirmations from removed multisig members still count toward the approval threshold, allowing a request to execute below the intended live-member threshold - (File: `multisig2/src/lib.rs`, `multisig/src/lib.rs`)

### Summary
Both the `multisig` and `multisig2` contracts remove a member's outstanding *requests* when that member/key is deleted, but they never scrub that member's existing *confirmations* recorded on other, still-pending requests. Because `confirm()` only compares the raw count of entries in the confirmations set against `num_confirmations`, a stale confirmation left behind by a removed member is indistinguishable from a confirmation by a currently-authorized member. This lets a pending request execute with fewer live-member confirmations than the configured threshold `K`.

### Finding Description
In `multisig2/src/lib.rs`, `delete_member` only purges requests *authored* by the removed member, and does nothing to the `confirmations` map entries the member left on requests authored by someone else: [1](#0-0) 

The `confirm()` function's threshold check simply counts set entries, with no validation that every entry still corresponds to a current member: [2](#0-1) 

`assert_valid_request` also never re-validates the confirmations set contents against `self.members`: [3](#0-2) 

The same pattern exists in the original `multisig` contract, where `DeleteKey` filters requests by `r.signer_pk == pk` (i.e. requests created by the removed key) but leaves that key's confirmation entries on other pending requests untouched: [4](#0-3) 

**Binding that should hold:** `confirmations.len() for request R == number of distinct *currently authorized* members who confirmed R`.

**What actually happens:** `confirmations.len()` includes entries from members who were later removed via `DeleteMember`/`DeleteKey`, so `confirmations.len() >= |live members who confirmed R|`. The threshold check `confirmations.len() + 1 >= num_confirmations` can be satisfied while the number of *live* confirmers is strictly less than `num_confirmations`.

### Impact Explanation
This is Critical: it can cause "a multisig request executed below threshold." Concretely: a member's access key is compromised and used to add a confirmation to a malicious pending `Transfer`/`FunctionCall`/`AddKey` request. The remaining honest members detect the compromise and respond by removing that member via `DeleteMember`/`DeleteKey` — the standard incident-response action. That removal does *not* strip the attacker's stale confirmation from the still-pending malicious request. If enough additional confirmations (real ones from honest members, or even other stale ones) accumulate to reach the nominal `num_confirmations` count, the request executes even though fewer live, currently-trusted members actually approved it than the threshold requires — moving funds, adding a full-access key, or deploying new code on the multisig account with insufficient live authorization.

### Likelihood Explanation
The precondition (a member/key is removed while it has outstanding confirmations on requests it did not create) is a normal, even expected, operational scenario — key rotation, offboarding, or security incident response. No special privilege is needed by the "attacker" beyond having once been a legitimate member/key holder (or having compromised one); the flaw is purely in the contract's bookkeeping, not in bypassing any access control on `confirm`/`delete_member` themselves.

### Recommendation
When removing a member (`delete_member` in `multisig2`, `DeleteKey`/`DeleteMember` handling in `multisig`), iterate over all entries in `self.confirmations` (not just requests authored by that member) and remove the member's identifier/public key from every confirmation set. Alternatively, validate at `confirm()`/execution time that every recorded confirmation still corresponds to a member present in `self.members`, discounting stale entries before comparing against `num_confirmations`.

### Proof of Concept
1. Initialize `multisig2` with members `[A, B, C, D]` and `num_confirmations = 3`.
2. `A` creates request `R` (e.g. `Transfer` to attacker-controlled account) via `add_request`.
3. `D`'s key is compromised; attacker calls `confirm(R)` as `D` → confirmations = `{A_impossible? }` — actually `A` calls `add_request` (not auto-confirmed), so have `A.confirm(R)` then `D.confirm(R)` → confirmations = `{A, D}` (2 of 3).
4. Honest members detect `D`'s compromise and submit/confirm a `DeleteMember { member: D }` request, which succeeds and removes `D` from `self.members` — but per `delete_member`'s logic (`multisig2/src/lib.rs:356-379`) it only deletes requests where `r.member == D` (i.e., requests *authored* by `D`); `R` was authored by `A`, so `R`'s confirmations set `{A, D}` is left untouched.
5. `B` (a legitimate, live member) calls `confirm(R)`. `confirmations.len() + 1 = 3 >= num_confirmations (3)` → `R` executes, even though only `A` and `B` (2 live members) actually authorized it — `D`'s now-invalid confirmation was counted, satisfying the threshold that should require 3 *live* confirmations.

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
