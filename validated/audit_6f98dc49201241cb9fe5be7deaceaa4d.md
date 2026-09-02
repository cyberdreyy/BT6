Confirmed: both `multisig/src/lib.rs` and `multisig2/src/lib.rs` have the same stale-confirmation flaw when a member/key is removed.

### Title
Removed multisig member's stale confirmations still count toward `num_confirmations`, allowing request execution below live threshold - (File: multisig2/src/lib.rs, multisig/src/lib.rs)

### Summary
When a multisig member (or access key) is deleted via `DeleteMember`/`DeleteKey`, the contract only purges requests *created by* that member/key. It never scans the `confirmations` sets of *other* requests to strip that member's prior confirmations. `confirm()` then counts those stale confirmations toward `num_confirmations` without verifying the confirming identities are still current members, so a request can execute with fewer live confirmations than the configured K-of-N threshold requires.

### Finding Description
In `multisig2/src/lib.rs`, `confirm()` checks `confirmations.len() as u32 + 1 >= self.num_confirmations` and, if satisfied, executes the request — with no check that every entry in `confirmations` is still a member of `self.members`: [1](#0-0) 

`delete_member` only removes confirmations for requests where `r.member == member` (i.e. requests that member *created*), not requests that member merely *confirmed*: [2](#0-1) 

The binding that should hold is: `confirmations counted for request R == confirmations from accounts currently in self.members`. After a `DeleteMember` execution, this equality breaks — a departed member's earlier confirmation is left in `confirmations[R]` and is still counted as live.

Concretely, with 3 members `{A, B, C}` and `num_confirmations = 2`:
1. `A` calls `add_request(R)` (creator recorded as `A`, zero confirmations).
2. `B` calls `confirm(R)` → `confirmations[R] = {B}` (1 < 2, not yet executed).
3. The multisig separately executes a `DeleteMember { member: B }` request (2 confirmations, unrelated to R) — `delete_member` only cleans requests *created by* `B`; `R` was created by `A`, so `B`'s entry in `confirmations[R]` is untouched.
4. `C` calls `confirm(R)` → `confirmations[R].len() (1, containing removed member B) + 1 (C) = 2 >= num_confirmations(2)` → `execute_request(R)` runs.

`R` executes with only one confirmation from a currently live member (`C`); `B`'s vote is stale and no longer represents an authorized signer. The 2-of-3 threshold guarantee is violated — the same flaw exists symmetrically in `multisig/src/lib.rs`'s `DeleteKey` handling, which filters `request_ids` by `r.signer_pk == pk` (the request creator) rather than scrubbing that key from all `confirmations` sets: [3](#0-2) 

### Impact Explanation
This is a Critical-impact analog of the "confirmations counted versus live members" custody binding: a multisig request can be executed below its configured signer threshold, e.g. moving funds (`Transfer`, `FunctionCall`, `AddKey`) with fewer genuinely live approvals than `num_confirmations` mandates. This directly undermines the K-of-N security guarantee the contract exists to enforce.

### Likelihood Explanation
No privileged bypass or social engineering is required beyond the multisig's own normal operating flow: any member who confirms a still-pending request and is later removed (for any reason — rotation, compromise response, off-boarding) leaves a live "ghost vote" that any remaining member can combine with fewer additional confirmations than intended to force execution. The precondition (an unconfirmed/partially-confirmed request outstanding at the time a confirming member is removed) is plausible in ordinary multisig lifecycle usage, not a contrived edge case.

### Recommendation
When executing `DeleteMember`/`DeleteKey`, iterate all outstanding `confirmations` entries (not just requests created by that member) and remove the departing member/key from each set. Alternatively, have `confirm()` re-validate at execution time that every account in a request's `confirmations` set is still a current member before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C]`, `num_confirmations = 2`.
2. `A` (as predecessor/signer) calls `add_request` with `receiver_id = self`, `actions = [Transfer{amount}]` → returns `request_id = R`.
3. `B` calls `confirm(R)` → stored, not executed (`confirmations[R] = {B}`).
4. Separately, `A` and `C` create+confirm a `DeleteMember{member: B}` request against `current_account_id()`, executing `delete_member` for `B` — note `B` is removed from `self.members`, but `confirmations[R]` is untouched because `R` was not created by `B`.
5. `C` calls `confirm(R)` → `confirmations[R].len() (1) + 1 = 2 >= num_confirmations (2)` → `execute_request(R)` fires the `Transfer`, even though only `C` (one live member) actually approved it post-removal.

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
