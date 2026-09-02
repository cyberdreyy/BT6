Analysis of the reported Solidity bug (`InvestmentManager::_deleteOffer` subtracting the wrong stored value, letting an accounting value diverge from the funds actually held) maps to an analogous defect in this repository's multisig contracts, where **confirmations counted toward quorum are not reconciled with the current, live set of members** when a member is deleted.

### Title
Stale confirmations from removed multisig members are still counted toward quorum, allowing a request to execute below the intended live-member threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` only purges pending requests and confirmations that were *originally submitted* by the member being removed. It does not scan and strip that member's confirmations from *other* pending requests that they had already confirmed. As a result, a confirmation cast by an account that is later removed from the multisig continues to count toward the `num_confirmations` threshold in `confirm`, letting a request execute with fewer *live* confirmations than the configured threshold requires.

### Finding Description
`confirm` decides whether to execute a request purely by counting entries in the `confirmations` set for that `request_id`: [1](#0-0) 

`delete_member` removes a departing member and revokes their access key, but it only cleans up requests they themselves created (matched via `r.member == member`); it does not touch the `confirmations` sets of requests created by *other* members that the departing member had previously confirmed: [2](#0-1) 

The equality this breaks is:
```
count(confirmations[request_id]) == count(live members who confirmed request_id)
```
After a member is deleted, any confirmation they cast before removal remains in the stored set, so the left side stays inflated relative to the right side. The same structural gap exists in the older `multisig` contract, where `DeleteKey` only wipes requests whose `signer_pk` matches the removed key, leaving that key's confirmations on other requests intact: [3](#0-2) [4](#0-3) 

### Impact Explanation
This is a Critical-class issue per the impact taxonomy: "a multisig request executed below threshold." A `Transfer`, `FunctionCall`, `AddKey`/`AddMember`, or `DeployContract` request can be pushed through with confirmations from fewer *current* members than `num_confirmations` mandates, because a stale confirmation from an already-removed member is silently counted. This weakens the K-of-N custody guarantee the multisig is supposed to enforce over the account's NEAR balance and permissions.

### Likelihood Explanation
The scenario is reachable through entirely ordinary usage, no privileged foundation or owner action beyond the multisig's own normal governance flow (member removal) is required:
1. Member A creates request `R` (e.g., a `Transfer`).
2. Member B (a legitimate, then-current member) confirms `R`. Confirmations = `{A, B}`.
3. The multisig later votes to remove member B via `DeleteMember` (for any legitimate reason: departure, suspected key compromise, rotation). `B` is removed from `self.members`, but `R`'s confirmation set still contains `B`.
4. Now only `A, C, D` remain as live members, so `num_confirmations` (say 3-of-3) is meant to require all three current members to agree.
5. Member `C` confirms `R`. `confirmations.len() + 1 == 3 >= num_confirmations`, so `execute_request` fires, even though only `A` and `C` (2 of 3 live members) actually approved it while live, B's stale approval effectively counts as a "ghost" vote.

Any member acting in bad faith can engineer or wait for this ordinary member-rotation sequence to slip a request past the intended live-member quorum.

### Recommendation
When removing a member (or key), scan **all** pending requests' confirmation sets, not just requests the member authored, and strip the departing member/key from every confirmation set:
```rust
fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
    ...
    let request_ids: Vec<u32> = self.requests.keys().collect();
    for request_id in request_ids {
        if let Some(mut confirmations) = self.confirmations.get(&request_id) {
            if confirmations.remove(&member.to_string()) {
                self.confirmations.insert(&request_id, &confirmations);
            }
        }
    }
    ...
}
```
Alternatively, validate at `confirm`-time that every entry in the stored confirmation set still belongs to `self.members` before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy `MultiSigContract::new(vec![A, B, C, D], 3)`.
2. `A` calls `add_request({Transfer to attacker_account, amount})` → request `R`.
3. `B` calls `confirm(R)` → `confirmations[R] = {A, B}` (count 2, no execution).
4. Members vote and execute a separate `DeleteMember{member: B}` request (reaches 3-of-4 quorum normally); `B` is removed from `self.members`, but `confirmations[R]` is untouched (still `{A, B}`) because `delete_member` only filters `self.requests` by `r.member == B`, and `R.member == A`.
5. `C` calls `confirm(R)`. `confirmations.len() (2) + 1 = 3 >= num_confirmations (3)` → `execute_request(R)` fires, transferring funds, even though only `A` and `C` are live members who actually confirmed while being current members (2 of the 3 live members `A, C, D`). [5](#0-4) [6](#0-5)

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

**File:** multisig2/src/lib.rs (L341-379)
```rust
    /// Add member to the list. Adds access key if member is key based.
    fn add_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        self.members.insert(&member.clone().into());
        match member {
            MultisigMember::AccessKey { public_key } => promise.add_access_key(
                public_key.into(),
                DEFAULT_ALLOWANCE,
                env::current_account_id(),
                MULTISIG_METHOD_NAMES.to_string(),
            ),
            MultisigMember::Account { account_id: _ } => promise,
        }
    }

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
