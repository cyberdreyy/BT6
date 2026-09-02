### Title
Stale confirmations from a removed multisig member are still counted toward the confirmation threshold - (File: multisig2/src/lib.rs)

### Summary
`delete_member` in the `multisig2` contract only purges *requests* that were created by the removed member; it never purges that member's *confirmations* recorded on other, still-pending requests created by someone else. Because `confirm` counts confirmations purely by size of the `confirmations` set without re-validating that every entry is still a current member, a request can later be executed using a confirmation that came from an account that is no longer a member.

### Finding Description
`delete_member` removes the member from `self.members`, deletes only the requests where `r.member == member` (i.e. requests they authored), and removes their `num_requests_pk` entry, but it does not scan `self.confirmations` to strip that member's confirmation from *other* pending requests: [1](#0-0) 

`confirm` decides whether to execute a request purely by comparing the size of the `confirmations` HashSet (plus the new confirmer) against `self.num_confirmations`, with no check that the accounts already present in `confirmations` are still members of `self.members`: [2](#0-1) 

`assert_valid_request` also only checks that the *caller* is currently a member and that the request/confirmation records exist — it never re-validates the identities already stored in `confirmations`: [3](#0-2) 

This is the same class of bug as the external report: a verification routine (`init` checking tier ranges / here, `confirm` checking confirmation count against membership) omits one necessary check — leaving one boundary/entry unvalidated — the last tier in the NFT case, the removed member's stale confirmations here.

### Impact Explanation
This breaks the "confirmations counted versus live members" custody binding described in the multisig's own security model: threshold `K` is supposed to represent `K` distinct *current* members agreeing, but a stale confirmation from a departed member can make up part of that `K`. A request (e.g. `Transfer`, `AddKey` with full permission, `DeployContract`) can therefore be executed with fewer than `K` confirmations from members who are current at execution time, letting a party who is no longer authorized to sign (or a colluding subset of current members plus one non-member's leftover confirmation) push through a `Transfer` action, effectively moving NEAR out of the multisig-controlled account below the intended threshold. This matches the Critical impact category: "a multisig request executed below threshold."

### Likelihood Explanation
This requires only unprivileged-but-legitimate multisig actions in a specific but realistic ordering: (1) a request R is created and confirmed by fewer than `K` current members, (2) a `DeleteMember` request removes one of R's confirmers (a normal governance action, not requiring any owner/foundation privilege beyond being an existing member), (3) a remaining member confirms R, pushing `confirmations.len()+1 >= num_confirmations` using the stale entry. No malicious deployment, no special key access, and no assumption violating documented usage is needed — it is a natural sequence of the contract's own supported operations (`add_request`, `confirm`, `DeleteMember`).

### Recommendation
In `delete_member` (and the analogous logic in `multisig/src/lib.rs`), iterate over `self.confirmations` for all pending requests and remove the deleted member's entry (converting to string form) from every confirmation set, not just from requests they authored. Alternatively, in `confirm`/`assert_valid_request`, filter `confirmations` to only members currently present in `self.members` before comparing against `num_confirmations`.

### Proof of Concept
1. Initialize `MultiSigContract::new(members = [A, B, C, D], num_confirmations = 3)`.
2. `A` calls `add_request_and_confirm(Transfer{...})` → request `R`, confirmations = `{A}`.
3. `B` calls `confirm(R)` → confirmations = `{A, B}` (2 < 3, not yet executed).
4. Members `A, C, D` create and confirm a `DeleteMember{member: B}` self-request (reaching threshold `3`), executing `delete_member` — this removes `B` from `self.members`, but `R`'s `confirmations` set still contains `B` since `R` was authored by `A`, not `B`, so it is left untouched (per `delete_member` logic at `multisig2/src/lib.rs:355-379`).
5. `D` (a legitimate current member who never touched `R` before) calls `confirm(R)`. Inside `confirm`, `confirmations.len() as u32 + 1 == 2 + 1 == 3 >= num_confirmations (3)` — the request executes, even though the currently-valid confirmers are only `A` and `D` (2 current members), with `B`'s stale confirmation making up the third "vote" despite `B` no longer being a member. [2](#0-1)

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
