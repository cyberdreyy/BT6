[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/multisig_account.move (L1185-1208)
```text
    /// Create a multisig transaction with a transaction hash instead of the full payload.
    /// This means the payload will be stored off chain for gas saving. Later, during execution, the executor will need
    /// to provide the full payload, which will be validated against the hash stored on-chain.
    public entry fun create_transaction_with_hash(
        owner: &signer,
        multisig_account: address,
        payload_hash: vector<u8>,
    ) {
        // Payload hash is a sha3-256 hash, so it must be exactly 32 bytes.
        assert!(payload_hash.length() == 32, error::invalid_argument(EINVALID_PAYLOAD_HASH));

        assert_multisig_account_exists(multisig_account);
        assert_is_owner(owner, multisig_account);

        let creator = address_of(owner);
        let transaction = MultisigTransaction {
            payload: option::none<vector<u8>>(),
            payload_hash: option::some(payload_hash),
            votes: simple_map::create<address, bool>(),
            creator,
            creation_time_secs: now_seconds(),
        };
        add_transaction(creator, multisig_account, transaction);
    }
```

**File:** aptos-move/framework/aptos-framework/sources/multisig_account.move (L1225-1253)
```text
    public entry fun vote_transanction(
        owner: &signer, multisig_account: address, sequence_number: u64, approved: bool) {
        assert_multisig_account_exists(multisig_account);
        let multisig_account_resource = borrow_global_mut<MultisigAccount>(multisig_account);
        assert_is_owner_internal(owner, multisig_account_resource);

        assert!(
            multisig_account_resource.transactions.contains(sequence_number),
            error::not_found(ETRANSACTION_NOT_FOUND),
        );
        let transaction = multisig_account_resource.transactions.borrow_mut(sequence_number);
        let votes = &mut transaction.votes;
        let owner_addr = address_of(owner);

        if (votes.contains_key(&owner_addr)) {
            *votes.borrow_mut(&owner_addr) = approved;
        } else {
            votes.add(owner_addr, approved);
        };

        emit(
            Vote {
                multisig_account,
                owner: owner_addr,
                sequence_number,
                approved,
            }
        );
    }
```
