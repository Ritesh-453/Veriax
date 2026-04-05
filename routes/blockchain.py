import hashlib
import json
import os
from datetime import datetime

BLOCKCHAIN_FILE = 'database/blockchain.json'

def load_chain():
    if os.path.exists(BLOCKCHAIN_FILE):
        try:
            with open(BLOCKCHAIN_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return []

def save_chain(chain):
    os.makedirs('database', exist_ok=True)
    with open(BLOCKCHAIN_FILE, 'w') as f:
        json.dump(chain, f, indent=2)

def calculate_hash(block):
    """Calculate SHA256 hash — always exclude 'hash' key before hashing"""
    block_copy = {k: v for k, v in block.items() if k != 'hash'}
    block_string = json.dumps(block_copy, sort_keys=True)
    return hashlib.sha256(block_string.encode()).hexdigest()

def create_genesis_block():
    genesis = {
        'index': 0,
        'timestamp': '2026-01-01T00:00:00',
        'type': 'GENESIS',
        'data': {
            'message': 'VERIAX Blockchain — Tamper-Proof Violation Log'
        },
        'previous_hash': '0' * 64,
        'hash': ''
    }
    genesis['hash'] = calculate_hash(genesis)
    return genesis

def get_last_block():
    chain = load_chain()
    if not chain:
        genesis = create_genesis_block()
        save_chain([genesis])
        return genesis
    return chain[-1]

def add_violation_block(asset_name, similarity, detection_methods,
                        found_url=None, scan_type='IMAGE'):
    chain = load_chain()
    if not chain:
        genesis = create_genesis_block()
        chain = [genesis]

    last_block = chain[-1]

    new_block = {
        'index': len(chain),
        'timestamp': datetime.now().isoformat(),
        'type': 'VIOLATION',
        'data': {
            'asset_name': asset_name,
            'similarity': similarity,
            'risk_level': get_risk(similarity),
            'detection_methods': detection_methods,
            'found_url': found_url,
            'scan_type': scan_type,
            'evidence_hash': hashlib.sha256(
                f"{asset_name}{similarity}{datetime.now().date()}".encode()
            ).hexdigest()[:16]
        },
        'previous_hash': last_block['hash'],
        'hash': ''
    }

    new_block['hash'] = calculate_hash(new_block)
    chain.append(new_block)
    save_chain(chain)
    print(f"Block #{new_block['index']} added — {asset_name} {similarity}%")
    return new_block

def add_asset_block(asset_name, filename, fingerprint):
    chain = load_chain()
    if not chain:
        genesis = create_genesis_block()
        chain = [genesis]

    last_block = chain[-1]

    new_block = {
        'index': len(chain),
        'timestamp': datetime.now().isoformat(),
        'type': 'ASSET_REGISTERED',
        'data': {
            'asset_name': asset_name,
            'filename': filename,
            'fingerprint': fingerprint[:16] + '...',
            'registered_by': 'VERIAX'
        },
        'previous_hash': last_block['hash'],
        'hash': ''
    }

    new_block['hash'] = calculate_hash(new_block)
    chain.append(new_block)
    save_chain(chain)
    return new_block

def verify_chain():
    """
    Verify blockchain integrity.
    Returns (True, message) if valid, (False, message) if tampered.
    """
    chain = load_chain()
    if not chain:
        return True, "Chain is empty"

    # Verify genesis block
    genesis_copy = {k: v for k, v in chain[0].items() if k != 'hash'}
    if chain[0]['hash'] != hashlib.sha256(
        json.dumps(genesis_copy, sort_keys=True).encode()
    ).hexdigest():
        return False, "Genesis block hash is invalid — TAMPERED!"

    for i in range(1, len(chain)):
        current = chain[i]
        previous = chain[i - 1]

        # Recalculate hash of current block (without 'hash' key)
        recalculated = calculate_hash(current)
        if current['hash'] != recalculated:
            return False, f"Block #{i} hash is invalid — TAMPERED!"

        # Check linkage
        if current['previous_hash'] != previous['hash']:
            return False, f"Block #{i} is not linked to Block #{i-1} — CHAIN BROKEN!"

    return True, f"Chain verified — {len(chain)} blocks intact"

def get_chain_stats():
    chain = load_chain()
    violations = [b for b in chain if b['type'] == 'VIOLATION']
    assets = [b for b in chain if b['type'] == 'ASSET_REGISTERED']

    valid, msg = verify_chain()

    return {
        'total_blocks': len(chain),
        'violation_blocks': len(violations),
        'asset_blocks': len(assets),
        'chain_valid': valid,
        'verify_message': msg,
        'latest_block': chain[-1]['hash'][:16] + '...' if chain else None
    }

def get_risk(similarity):
    if similarity >= 90: return 'CRITICAL'
    if similarity >= 70: return 'HIGH'
    if similarity >= 50: return 'MEDIUM'
    return 'LOW'