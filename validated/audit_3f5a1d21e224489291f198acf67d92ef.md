### Title
Stale Confirmations From Removed Multisig Members/Keys Are Still Counted Toward the Approval Threshold, Allowing Requests to Execute Below the Live-Member Threshold - ([File: multisig2/src/lib.rs], [File: multisig/src/lib.rs])

### Summary
`DeleteMember` (multisig2) and `DeleteKey` (multisig) only purge pending requests that were *created* by the removed member/key. They do not scrub confirmations that member/key previously cast on requests created by *other* members. Those stale confirmations remain in the `confirmations` set and are still counted by `confirm()` when comparing against `num_confirmations`. This lets a request execute with fewer live, currently-authorized approvals than the configured threshold requires.

### Finding Description
The binding the multisig is supposed to enforce is:

`confirmations_counted(request) == confirmations_from_current_live_members(request)`

and requests should execute only once `confirmations_counted(request) >= num_confirmations` where all counted confirmations come from accounts/keys that are still members at execution time.

In `multisig2/src/lib.rs`, `delete_member` only removes requests whose *originator* (`r.member`) equals the member being deleted: [1](#0-0) 

It never inspects `self.confirmations` entries for other, still-pending requests that the removed member previously confirmed. Those confirmation strings (keyed by `member.to_string()`) are left untouched.

`confirm()` simply compares the size of the (unfiltered) confirmation set to `num_confirmations` and executes once the threshold is met: [2](#0-1) 

`assert_valid_request` / `current_member` only validate that the *caller* confirming right now is a current member; they never re-validate the *existing* entries already stored in the confirmation set: [3](#0-2) 

The same pattern exists in the original `multisig/src/lib.rs`: `DeleteKey` removes only requests created by the deleted key, leaving that key's confirmations on other requests intact, and `confirm()` counts the raw confirmation set size against `num_confirmations`: [4](#0-3) 

Because the number of *live* members can shrink (a member/key is removed, e.g. due to compromise or intentional rotation) while stale confirmation entries persist, the effective threshold can drop below what the current member set intends.

### Impact Explanation
This matches the Critical impact category "a multisig request executed below threshold." A request can be approved and executed with fewer currently-authorized confirmations than `num_confirmations` mandates, because a removed member's/key's earlier confirmation is still counted. This directly undermines the core security guarantee of the multisig contract — that any state-changing action (including `Transfer`, `DeployContract`, `AddKey`/`AddMember`, `FunctionCall`) requires `num_confirmations` currently-valid signers. This is particularly dangerous in the exact scenario multisig membership changes are meant to protect against: removing a member believed to be compromised. Even after removal, their prior confirmation on a not-yet-executed request still helps push it over the threshold.

### Likelihood Explanation
This requires no special privilege beyond normal multisig operation flow: any member can add/confirm a request, and the deployer/members can execute a routine `DeleteMember`/`DeleteKey` action (a normal, expected multisig maintenance operation, not misuse). Any sequence where a request is confirmed by fewer than `num_confirmations` members before one confirmer is removed, followed by remaining members confirming to reach nominal count, triggers the flaw. This is a plausible, low-effort real-world sequence (rotate a key/member while requests are outstanding), making likelihood moderate-to-high.

### Recommendation
When deleting a member/key, iterate over **all** pending requests' confirmation sets (not just requests they originated) and remove the deleted member's/key's string from every `confirmations` entry. Alternatively, when confirming, filter the stored confirmation set to only currently-valid members before comparing the count to `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with 3 members `A, B, C` and `num_confirmations = 2`.
2. `C` calls `add_request` to create request `R1` (e.g., `Transfer` to an external account). `R1` has zero confirmations from `C` itself.
3. `B` calls `confirm(R1)`. Confirmation set for `R1` = `{B}` (1 of 2 required).
4. Members execute a routine `DeleteMember { member: B }` request (via its own multisig flow, e.g., confirmed by `A` and `C`) to remove `B` (perhaps because `B`'s key is suspected compromised). `delete_member` only removes requests *created by* `B`; `R1` (created by `C`) is untouched, so `R1`'s confirmation set still contains `B`.
5. `A` calls `confirm(R1)`. `confirmations.len() + 1 = 2 >= num_confirmations (2)` → `R1` executes the `Transfer`, even though only one currently-live member (`A`) actually approved it after `B` was removed. [2](#0-1) [1](#0-0)

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

**File:** multisig/src/lib.rs (L198-266)
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
                MultiSigRequestAction::FunctionCall {
                    method_name,
                    args,
                    deposit,
                    gas,
                } => promise.function_call(
                    method_name.into_bytes(),
                    args.into(),
                    deposit.into(),
                    gas.into(),
                ),
                // the following methods must be a single action
                MultiSigRequestAction::SetNumConfirmations { num_confirmations } => {
                    self.assert_one_action_only(receiver_id, num_actions);
                    self.num_confirmations = num_confirmations;
                    return PromiseOrValue::Value(true);
                }
                MultiSigRequestAction::SetActiveRequestsLimit {
                    active_requests_limit,
                } => {
                    self.assert_one_action_only(receiver_id, num_actions);
                    self.active_requests_limit = active_requests_limit;
                    return PromiseOrValue::Value(true);
                }
            };
        }
        promise.into()
    }

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
