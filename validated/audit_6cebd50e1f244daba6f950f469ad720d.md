This confirms the vulnerability. In `multisig2/src/lib.rs`, `add_request` requires the caller to be a `current_member` [1](#0-0) , and `confirm` counts entries in the `confirmations: HashSet<String>` set without re-validating that each entry is still a live member [2](#0-1) . `delete_member` only purges `requests`/`confirmations` for requests where the removed member was the original *submitter* (`r.member == member`); it never scans other pending requests' `confirmations` sets to strip out a stale approval from a member being removed [3](#0-2) . The identical pattern exists in `multisig/src/lib.rs`'s `DeleteKey` handling, which filters `requests` by `r.signer_pk == pk` (the request's original signer) but does not purge `pk` out of other requests' `confirmations: HashSet<PublicKey>` [4](#0-3) , and `confirm()` similarly just compares `confirmations.len()` against `num_confirmations` [5](#0-4) .

### Title
Stale confirmations from removed multisig members are counted toward the approval threshold, allowing request execution below the live-member quorum - ([File: multisig2/src/lib.rs], [File: multisig/src/lib.rs])

### Summary
Both the `multisig` and `multisig2` contracts implement a K-of-N approval scheme where a `MultiSigRequest` executes once `confirmations.len() + 1 >= num_confirmations`. When a member/key is removed via `DeleteMember`/`DeleteKey`, the contract only clears confirmation state for requests where the removed member was the *original submitter*. It does not scan and strip the removed member's approval from other pending requests they had merely co-confirmed. Because `confirm()` blindly counts set membership rather than validating each confirming party is still a current member, a stale confirmation continues to count toward the threshold indefinitely.

### Finding Description
The intended invariant is: `live_member_confirmations(request) >= num_confirmations` before a request (e.g., a `Transfer`, `AddKey`, `FunctionCall`) can execute.

In `multisig2/src/lib.rs`:
- `confirm()` reads `self.confirmations.get(&request_id)` (a `HashSet<String>` of member identifiers) and executes once `confirmations.len() as u32 + 1 >= self.num_confirmations`, with no check that the stored identifiers are still in `self.members` [2](#0-1) .
- `delete_member()` removes the member from `self.members`, but only deletes `requests`/`confirmations` entries for requests where `r.member == member` (i.e., requests the removed member had *submitted*) [6](#0-5) . Any other pending request that this member had previously called `confirm()` on retains that member's string inside its `confirmations` set.

The equivalent path exists in `multisig/src/lib.rs`'s `MultiSigRequestAction::DeleteKey` handling, which filters and purges only requests where `r.signer_pk == pk` [4](#0-3) , leaving stale public keys inside other requests' `confirmations` sets, which `confirm()` again counts purely by length [5](#0-4) .

The binding broken as an equality:
`confirmations_recorded_on_request == live_members_who_approved` — this assumption fails once any confirmer is subsequently removed from the multisig; `confirmations_recorded_on_request` remains inflated by the departed member's stale entry.

### Impact Explanation
This lets a request (including `Transfer`, `FunctionCall`, `AddKey`, or `DeployContract`) execute with fewer live-member approvals than `num_confirmations` mandates. Since these multisig contracts are the documented custody mechanism for lockup/DAO-style accounts (per `lockup/README.md`'s reliance on staking pool/whitelist/multisig guarantees), this is a "multisig request executed below threshold" event — funds can move, or an unauthorized `FunctionCall`/`AddKey` can be executed, with the true quorum of currently-trusted signers never having been met. This falls squarely into the Critical impact category defined by the rules.

### Likelihood Explanation
This does not require any privileged actor beyond ordinary multisig operation: a member submits/confirms an ordinary request, then later that member (or another) is removed through the normal `DeleteMember`/`DeleteKey` self-request flow (a routine key-rotation/offboarding action), and any remaining request that the removed member had confirmed keeps counting toward quorum. No malicious deployment, no privileged escalation beyond what any member set already can do, and no protocol assumption violation is required — only the ordinary sequence of confirm-then-remove-member, which is a foreseeable operational pattern (e.g., rotating out a compromised or departing signer).

### Recommendation
When executing `DeleteMember`/`DeleteKey`, iterate over *all* pending requests' `confirmations` sets (not just those the removed member submitted) and remove the departing member's/key's entry from each, re-evaluating whether the request still meets the quorum. Alternatively, have `confirm()` (and any threshold check) filter `confirmations` against `self.members` (or currently valid access keys) before comparing against `num_confirmations`, ensuring only live members' approvals are counted.

### Proof of Concept
1. Initialize `multisig2` with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `B` calls `add_request_and_confirm(R)` where `R` is a `Transfer` to an attacker-controlled account — `confirmations[R] = {B}`.
3. `A` calls `confirm(R)` — `confirmations[R] = {A, B}` (2 of 3, request not yet executed).
4. Separately, members `B`, `C`, `D` confirm a `DeleteMember{A}` self-request (meeting the 3-of-4 quorum honestly) — `A` is removed from `self.members`; `delete_member()` only clears requests where `A` was the *submitter*, so `confirmations[R] = {A, B}` is untouched because `B`, not `A`, submitted `R`.
5. `C` (still a live member) calls `confirm(R)` — `confirmations[R].len() + 1 == 3 >= num_confirmations(3)`, so `R` executes, transferring funds, even though only `B` and `C` are live members who genuinely approved; `A`'s stale confirmation was needed to reach quorum.

### Citations

**File:** multisig2/src/lib.rs (L169-175)
```rust
    /// Add request for multisig.
    pub fn add_request(&mut self, request: MultiSigRequest) -> RequestId {
        let current_member = self.current_member().unwrap_or_else(|| {
            env::panic_str(
                "Predecessor must be a member or transaction signed with key of given account",
            )
        });
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
