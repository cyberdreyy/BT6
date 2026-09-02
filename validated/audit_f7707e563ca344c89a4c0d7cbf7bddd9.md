## Title
Stale confirmations from deleted multisig members are still counted toward the confirmation threshold, allowing requests to execute below the configured `num_confirmations` - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` in `multisig2/src/lib.rs` only checks the size of the `confirmations` `HashSet<String>` associated with a request against `num_confirmations`. When a member is removed via `DeleteMember`, `delete_member` only purges requests and confirmations for requests that member itself originated (`r.member == member`), but does **not** scan and strip that member's confirmation entries from *other* pending requests it had confirmed. Those stale confirmations remain in the `confirmations` set and continue to count toward the K-of-N threshold even though the confirming account/key is no longer a member.

### Finding Description
The binding that should hold is: `live confirmations from current members >= num_confirmations` before a request executes. Instead, the code enforces: `confirmations.len() (including entries from members removed after confirming) >= num_confirmations`.

- `confirm()` reads the `HashSet<String>` for the request and executes once `confirmations.len() as u32 + 1 >= self.num_confirmations`, with no re-validation that every entry in the set corresponds to a currently active member: [1](#0-0) 
- `delete_member()` removes the member from `self.members` and deletes only requests where the request's originating `member` field equals the deleted member. It does not remove that member's confirmation string from confirmation sets of requests originated by *other* members: [2](#0-1) 

Sequence to break the binding (contract with members `[A, B, C, D]`, `num_confirmations = 3`):
1. `A` calls `add_request_and_confirm(R)` → `confirmations(R) = {A}`.
2. `B` calls `confirm(R)` → `confirmations(R) = {A, B}` (2 < 3, not yet executed).
3. Separately, a `DeleteMember { member: B }` request is created and confirmed by 3 *other* live members (e.g., A, C, D) and executes, removing `B` from `self.members`. Since `R` was originated by `A` (not `B`), `delete_member` does not touch `confirmations(R)`; `B`'s entry stays in the set.
4. `C` (now the third live member relevant to `R`) calls `confirm(R)` → `confirmations.len() as u32 + 1 == 3 >= num_confirmations (3)` → `execute_request(R)` runs.

`R` executes with confirmations `{A, B, C}`, but `B` is no longer a member — only 2 out of the 3 confirmations came from currently-live members. The K-of-N guarantee documented in the README ("Any of the access keys or set of specified accounts can confirm, until the required number of confirmation achieved") is violated because the "required number" is measured against stale, no-longer-authorized confirmers.

### Impact Explanation
This breaks the authorization threshold binding of the multisig: a request (which can be an arbitrary `Transfer`, `FunctionCall`, `AddKey`, `AddMember`, etc.) executes despite not having the required number of confirmations from members who are actually part of the multisig at execution time. This directly matches the in-scope Critical impact "a multisig request executed below threshold," since funds or privileged actions (adding a full-access key, transferring NEAR, deploying new contract code) can be pushed through with effectively fewer than `num_confirmations` live approvals.

### Likelihood Explanation
This requires only ordinary multisig operation timing (create/confirm a request, then have a `DeleteMember` request for one of the partial confirmers execute before the original request reaches threshold) — no privileged foundation action, no redeploy, and no key compromise. Any member set that changes membership while requests are pending (a normal, expected operational pattern) is exposed. The same root cause is present in the legacy `multisig/src/lib.rs` `DeleteKey` handling, which likewise deletes only requests originated by the deleted key: [3](#0-2) 

### Recommendation
When deleting a member/key, iterate over all pending requests' confirmation sets (not just requests originated by that member) and remove the deleted member's entry from every confirmation set. Alternatively, validate at `confirm()`/execution time that every string in the confirmation set still corresponds to a member currently in `self.members` before comparing against `num_confirmations`.

### Proof of Concept
Given the existing test harness in `multisig2/src/lib.rs` (`members()`, `context_with_key`/`context_with_account` helpers): [4](#0-3) 

1. Create `MultiSigContract::new(members(), 3)` with members `[alice(account), bob(account), key1, key2]`.
2. As `key1`, `add_request_and_confirm(Transfer{...})` → `confirmations = {key1}`.
3. As `bob`, `confirm(request_id)` → `confirmations = {key1, bob}` (2/3).
4. As `alice`, submit and confirm a `DeleteMember{ member: bob }` request that reaches 3 confirmations (e.g., alice, key1, key2) and executes, removing `bob` from `self.members`. Because the transfer request in step 2 was originated by `key1` not `bob`, `delete_member` leaves `confirmations(request_id)` untouched.
5. As `key2`, call `confirm(request_id)` → `confirmations.len()+1 == 3 >= num_confirmations` → the transfer executes, even though only `key1` and `key2` are live confirmers plus a stale `bob` entry — i.e., the transfer executed with confirmations that do not correspond to 3 currently-live members.

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

**File:** multisig2/src/lib.rs (L513-571)
```rust
    fn members() -> Vec<MultisigMember> {
        vec![
            MultisigMember::Account {
                account_id: alice(),
            },
            MultisigMember::Account { account_id: bob() },
            MultisigMember::AccessKey {
                public_key: PublicKey::from(
                    "ed25519:Eg2jtsiMrprn7zgKKUk79qM1hWhANsFyE6JSX4txLEuy"
                        .parse()
                        .unwrap(),
                ),
            },
            MultisigMember::AccessKey {
                public_key: PublicKey::try_from(TEST_KEY.to_vec()).unwrap(),
            },
        ]
    }

    fn context_with_key(key: PublicKey, amount: Balance) -> VMContext {
        context_with_account_key(alice(), key, amount)
    }

    fn context_with_account(account_id: AccountId, amount: Balance) -> VMContext {
        context_with_account_key(
            account_id,
            PublicKey::try_from(vec![
                0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23,
                24, 25, 26, 27, 28, 29, 30, 31, 32, 33,
            ])
            .unwrap(),
            amount,
        )
    }

    fn context_with_account_key(
        account_id: AccountId,
        key: PublicKey,
        amount: Balance,
    ) -> VMContext {
        VMContextBuilder::new()
            .current_account_id(alice())
            .predecessor_account_id(account_id.clone())
            .signer_account_id(account_id.clone())
            .signer_account_pk(key)
            .account_balance(amount)
            .build()
    }

    fn context_with_key_future(key: PublicKey, amount: Balance) -> VMContext {
        VMContextBuilder::new()
            .current_account_id(alice())
            .block_timestamp(REQUEST_COOLDOWN + 1)
            .predecessor_account_id(alice())
            .signer_account_id(alice())
            .signer_account_pk(key)
            .account_balance(amount)
            .build()
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
