### Title
Multisig executes requests using confirmations from members already removed via `DeleteMember` - (File: `multisig2/src/lib.rs`)

### Summary
The `MultiSigContract::confirm` function in `multisig2/src/lib.rs` counts confirmations stored in the `confirmations: LookupMap<RequestId, HashSet<String>>` map to decide whether a request has reached `num_confirmations` and should execute. When a member is removed via `DeleteMember` (handled in `delete_member`), the code only purges confirmations for requests that were *originated* by the removed member (`r.member == member`). It does **not** scrub that member's string identifier out of the `confirmations` set of any *other* pending request that member had previously confirmed. As a result, a stale confirmation from an account that is no longer a multisig member remains counted toward the execution threshold, letting a request execute with fewer live (currently valid) confirmations than `num_confirmations` actually requires.

### Finding Description
The threshold invariant the multisig is supposed to enforce is:

`count(confirmations by currently-valid members) >= num_confirmations`

But the code effectively implements:

`count(confirmations ever recorded, including by since-removed members) >= num_confirmations`

Relevant code: [1](#0-0) 

`confirm` only checks `confirmations.len() as u32 + 1 >= self.num_confirmations` against the raw `HashSet<String>` for the request; it never filters this set against `self.members`. [2](#0-1) 

`delete_member` removes:
- `num_requests_pk` entry for the removed member,
- the member itself from `self.members`,
- requests **originated by** the removed member (`r.member == member`) and their confirmations,

but it never iterates over confirmations of requests that were *created by other members but confirmed by the member being deleted*. Those stale entries (serialized `MultisigMember` strings) persist in `self.confirmations`.

Concrete scenario:
1. Multisig has members `{A, B, C, D}`, `num_confirmations = 3`.
2. `A` creates request `R` (`add_request`), then `A` and `B` confirm it (`confirmations = {A, B}`, 2/3).
3. The multisig determines `B`'s key is compromised and removes `B` via a separate, properly-confirmed `DeleteMember{B}` request. Members are now `{A, C, D}`; `delete_member` does not touch `R`'s confirmation set because `R.member == A`, not `B`.
4. `C` calls `confirm(R)`. `confirmations.len() (2) + 1 = 3 >= num_confirmations (3)` → request executes.

`R` executes with the "confirmation" of only `A` and `C` as live members plus a stale confirmation from `B`, who is no longer part of the multisig at execution time. Only 2 of the 3 currently-authorized members actually agreed to `R`, breaking the K-of-N authorization guarantee for whatever `MultiSigRequestAction` `R` contains (e.g., `Transfer`, `FunctionCall`, `AddKey`, `AddMember`).

### Impact Explanation
This crosses the "confirmations counted versus live members" custody binding explicitly in scope. A multisig request (including `Transfer` of NEAR/NEP-141 held by the account, or `AddKey`/`AddMember` actions that grant control) executes below the actual live-member threshold. This maps to the Critical impact category: "a multisig request executed below threshold." Funds controlled by the multisig account can be moved, or control of the account can be escalated (e.g., adding a new full-access key), with fewer genuine approvals than `num_confirmations` mandates — precisely because a removed member's stale confirmation is still counted.

### Likelihood Explanation
This requires no attacker-controlled deployment or malicious node — only the ordinary, documented multisig lifecycle: a member confirms a request, is later removed via `DeleteMember` (a routine security operation, e.g. rotating out a compromised or departing member), and a remaining member confirms afterward. This is a realistic and even expected operational sequence (removing a member is the standard response to key compromise), making the stale-confirmation-based under-threshold execution readily triggerable without any privileged bypass — it is a code defect in `delete_member`/`confirm`, not a misuse of privilege.

### Recommendation
When executing `DeleteMember`, iterate over all pending requests' `confirmations` sets (not just requests originated by the deleted member) and remove the deleted member's identifier from each. Alternatively, in `confirm`, filter the stored confirmation set against `self.members` before comparing its length to `num_confirmations`, so confirmations from accounts/keys that are no longer members never count toward the threshold.

### Proof of Concept
Using the existing test harness in `multisig2/src/lib.rs`:
1. `MultiSigContract::new(members(), 3)` with members `{alice, bob, key1, key2}`.
2. As `alice`, `add_request(Transfer{...})` → `request_id`, then `confirm(request_id)` (1/3).
3. As `key1`, `confirm(request_id)` (2/3, confirmations = `{alice, key1}`).
4. Via a separate fully-confirmed request, execute `DeleteMember{member: key1}` (this only cleans requests where `r.member == key1`, not the pending `Transfer` request created by `alice`).
5. As `bob`, `confirm(request_id)`: `confirmations.len() (2) + 1 = 3 >= num_confirmations (3)` → `execute_request` runs the `Transfer`, even though `key1` was removed before this final confirmation and only `alice` and `bob` are genuinely live approvers. [2](#0-1) [1](#0-0)

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
