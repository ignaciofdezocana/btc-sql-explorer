#!/usr/bin/env python3
"""
Bitcoin Blockchain Parser

This script reads a Bitcoin blockchain file (blk.dat) and converts it into four Parquet files:
- blocks.parquet: Block information
- transactions.parquet: Transaction information  
- transaction_inputs.parquet: Transaction input details
- transaction_outputs.parquet: Transaction output details

Usage: python btc_parser.py [blk_file_path]
"""

import sys
import os
import pandas as pd
from tqdm import tqdm
import bitcoin.core
import bitcoin.core.script
from bitcoin.core import CBlock, CTransaction, CTxIn, CTxOut
from bitcoin.core.script import CScript
import struct
import hashlib
import base58

# Bitcoin mainnet magic bytes
MAGIC_BYTES = b'\xf9\xbe\xb4\xd9'

def extract_addresses_from_script(script, is_output=True):
    """
    Extract Bitcoin addresses from a script (scriptPubKey for outputs, scriptSig for inputs)
    
    Args:
        script: CScript object
        is_output: True if this is a scriptPubKey (output), False if scriptSig (input)
    
    Returns:
        list: List of addresses found in the script
    """
    addresses = []
    
    try:
        if is_output:
            # For outputs, we extract from scriptPubKey
            script_asm = script.hex()
            
            # P2PKH (Pay to Public Key Hash) - starts with 76a914...88ac
            if script_asm.startswith('76a914') and script_asm.endswith('88ac'):
                pubkey_hash = script_asm[6:-4]  # Remove 76a914 and 88ac
                try:
                    # Convert to base58 address
                    addr = base58.b58encode_check(b'\x00' + bytes.fromhex(pubkey_hash)).decode('ascii')
                    addresses.append(addr)
                except:
                    pass
            
            # P2SH (Pay to Script Hash) - starts with a914...87
            elif script_asm.startswith('a914') and script_asm.endswith('87'):
                script_hash = script_asm[4:-2]  # Remove a914 and 87
                try:
                    # Convert to base58 address
                    addr = base58.b58encode_check(b'\x05' + bytes.fromhex(script_hash)).decode('ascii')
                    addresses.append(addr)
                except:
                    pass
            
            # P2PK (Pay to Public Key) - starts with 41...ac
            elif script_asm.startswith('41') and script_asm.endswith('ac'):
                pubkey = script_asm[2:-2]  # Remove 41 and ac
                try:
                    # Hash the public key
                    pubkey_bytes = bytes.fromhex(pubkey)
                    pubkey_hash = hashlib.sha256(pubkey_bytes).digest()
                    ripemd160_hash = hashlib.new('ripemd160', pubkey_hash).digest()
                    addr = base58.b58encode_check(b'\x00' + ripemd160_hash).decode('ascii')
                    addresses.append(addr)
                except:
                    pass
            
            # OP_RETURN - starts with 6a
            elif script_asm.startswith('6a'):
                addresses.append('OP_RETURN')
            
            # Multi-sig (OP_1 to OP_16 followed by public keys and OP_CHECKMULTISIG)
            elif 'ae' in script_asm:  # OP_CHECKMULTISIG
                addresses.append('MULTISIG')
            
            # Witness programs (P2WPKH, P2WSH) - starts with 0014 or 0020
            elif script_asm.startswith('0014'):
                witness_hash = script_asm[4:]  # Remove 0014
                try:
                    # Convert to bech32 address (simplified - would need bech32 library)
                    addresses.append(f'WITNESS_V0_KEYHASH_{witness_hash[:8]}...')
                except:
                    pass
            elif script_asm.startswith('0020'):
                witness_hash = script_asm[4:]  # Remove 0020
                try:
                    addresses.append(f'WITNESS_V0_SCRIPTHASH_{witness_hash[:8]}...')
                except:
                    pass
        
        else:
            # For inputs, we try to extract from scriptSig
            # This is more limited since scriptSig often contains signatures
            script_asm = script.hex()
            
            # If it's a coinbase transaction, mark it
            if script_asm.startswith('03') or script_asm.startswith('04'):
                addresses.append('COINBASE')
            else:
                # Try to extract public key from scriptSig
                # This is complex and often not possible without UTXO lookup
                addresses.append('UNKNOWN_INPUT')
    
    except Exception as e:
        # If we can't parse the script, return empty list
        pass
    
    return addresses

def get_script_type(script):
    """
    Determine the type of script (P2PKH, P2SH, P2PK, etc.)
    """
    try:
        script_asm = script.hex()
        
        if script_asm.startswith('76a914') and script_asm.endswith('88ac'):
            return 'P2PKH'
        elif script_asm.startswith('a914') and script_asm.endswith('87'):
            return 'P2SH'
        elif script_asm.startswith('41') and script_asm.endswith('ac'):
            return 'P2PK'
        elif script_asm.startswith('6a'):
            return 'OP_RETURN'
        elif 'ae' in script_asm:
            return 'MULTISIG'
        elif script_asm.startswith('0014'):
            return 'P2WPKH'
        elif script_asm.startswith('0020'):
            return 'P2WSH'
        else:
            return 'UNKNOWN'
    except:
        return 'UNKNOWN'

def read_varint(f):
    """Read a variable length integer from file"""
    first_byte = f.read(1)[0]
    if first_byte < 0xfd:
        return first_byte
    elif first_byte == 0xfd:
        return struct.unpack('<H', f.read(2))[0]
    elif first_byte == 0xfe:
        return struct.unpack('<I', f.read(4))[0]
    else:
        return struct.unpack('<Q', f.read(8))[0]

def read_block(f):
    """Read a single block from the file"""
    # Read magic bytes
    magic = f.read(4)
    if not magic or magic != MAGIC_BYTES:
        return None
    
    # Read block size
    block_size = struct.unpack('<I', f.read(4))[0]
    
    # Read block data
    block_data = f.read(block_size)
    if len(block_data) != block_size:
        return None
    
    return block_data

def parse_block(block_data, block_number):
    """Parse a block and extract all data"""
    try:
        block = CBlock.deserialize(block_data)
        block_hash = block.GetHash()
        
        # Calculate block metrics
        block_size = len(block_data)
        stripped_size = block_size - 80  # Remove header size
        weight = stripped_size * 3 + block_size  # Simplified weight calculation
        
        # Extract coinbase transaction
        coinbase_tx = block.vtx[0]
        coinbase_param = ""
        if coinbase_tx.vin and coinbase_tx.vin[0].scriptSig:
            coinbase_param = coinbase_tx.vin[0].scriptSig.hex()
        
        # Block data
        block_info = {
            'hash': block_hash.hex(),
            'size': block_size,
            'stripped_size': stripped_size,
            'weight': weight,
            'number': block_number,
            'version': block.nVersion,
            'merkle_root': block.hashMerkleRoot.hex(),
            'timestamp': block.nTime,
            'nonce': hex(block.nNonce),
            'bits': hex(block.nBits),
            'coinbase_param': coinbase_param,
            'transaction_count': len(block.vtx)
        }
        
        transactions = []
        transaction_inputs = []
        transaction_outputs = []
        
        for tx_index, tx in enumerate(block.vtx):
            tx_hash = tx.GetHash()
            
            # Calculate transaction metrics
            tx_size = len(tx.serialize())
            virtual_size = tx_size  # Simplified virtual size
            
            # Calculate input/output values
            input_value = 0
            output_value = sum(out.nValue for out in tx.vout)
            
            # Transaction data
            tx_info = {
                'hash': tx_hash.hex(),
                'size': tx_size,
                'virtual_size': virtual_size,
                'version': tx.nVersion,
                'lock_time': tx.nLockTime,
                'block_number': block_number,
                'block_hash': block_hash.hex(),
                'block_timestamp': block.nTime,
                'is_coinbase': tx_index == 0,
                'index': tx_index,
                'input_count': len(tx.vin),
                'output_count': len(tx.vout),
                'input_value': input_value,
                'output_value': output_value,
                'fee': output_value - input_value
            }
            transactions.append(tx_info)
            
            # Process inputs
            for input_index, txin in enumerate(tx.vin):
                # Extract addresses from scriptSig
                addresses = extract_addresses_from_script(txin.scriptSig, is_output=False)
                script_type = get_script_type(txin.scriptSig)
                
                input_info = {
                    'transaction_hash': tx_hash.hex(),
                    'index': input_index,
                    'spent_transaction_hash': txin.prevout.hash.hex(),
                    'spent_output_index': txin.prevout.n,
                    'script_asm': script_type,
                    'script_hex': txin.scriptSig.hex(),
                    'sequence': txin.nSequence,
                    'required_signatures': 1,  # Most inputs require 1 signature
                    'type': script_type,
                    'addresses': addresses,
                    'value': 0  # Would need UTXO lookup to get actual value
                }
                transaction_inputs.append(input_info)
            
            # Process outputs
            for output_index, txout in enumerate(tx.vout):
                # Extract addresses from scriptPubKey
                addresses = extract_addresses_from_script(txout.scriptPubKey, is_output=True)
                script_type = get_script_type(txout.scriptPubKey)
                
                # Determine required signatures based on script type
                required_signatures = 1
                if script_type == 'MULTISIG':
                    # Try to determine from script (simplified)
                    script_asm = txout.scriptPubKey.hex()
                    if 'ae' in script_asm:
                        required_signatures = 2  # Default for multisig
                
                output_info = {
                    'transaction_hash': tx_hash.hex(),
                    'index': output_index,
                    'script_asm': script_type,
                    'script_hex': txout.scriptPubKey.hex(),
                    'required_signatures': required_signatures,
                    'type': script_type,
                    'addresses': addresses,
                    'value': txout.nValue
                }
                transaction_outputs.append(output_info)
        
        return block_info, transactions, transaction_inputs, transaction_outputs
        
    except Exception as e:
        print(f"Error parsing block {block_number}: {e}")
        return None, [], [], []

def main():
    # Get file path from command line or use default
    if len(sys.argv) > 1:
        blk_file_path = sys.argv[1]
    else:
        blk_file_path = "sample_blk.dat"
    
    if not os.path.exists(blk_file_path):
        print(f"Error: File {blk_file_path} not found")
        sys.exit(1)
    
    print(f"Parsing Bitcoin blockchain file: {blk_file_path}")
    
    # Initialize data structures
    all_blocks = []
    all_transactions = []
    all_inputs = []
    all_outputs = []
    
    block_number = 0
    
    # Get file size for progress bar
    file_size = os.path.getsize(blk_file_path)
    
    with open(blk_file_path, 'rb') as f:
        with tqdm(total=file_size, unit='B', unit_scale=True, desc="Parsing blocks") as pbar:
            while True:
                start_pos = f.tell()
                
                # Try to read a block
                block_data = read_block(f)
                if not block_data:
                    break
                
                # Parse the block
                block_info, transactions, inputs, outputs = parse_block(block_data, block_number)
                
                if block_info:
                    all_blocks.append(block_info)
                    all_transactions.extend(transactions)
                    all_inputs.extend(inputs)
                    all_outputs.extend(outputs)
                    block_number += 1
                
                # Update progress bar
                current_pos = f.tell()
                pbar.update(current_pos - start_pos)
    
    print(f"\nParsed {block_number} blocks")
    print(f"Found {len(all_transactions)} transactions")
    print(f"Found {len(all_inputs)} transaction inputs")
    print(f"Found {len(all_outputs)} transaction outputs")
    
    # Create DataFrames and save to Parquet
    print("\nWriting Parquet files...")
    
    # Blocks Parquet
    blocks_df = pd.DataFrame(all_blocks)
    blocks_df.to_parquet('blocks.parquet', index=False, compression='zstd')
    print(f"✓ blocks.parquet written ({len(blocks_df)} rows)")
    
    # Transactions Parquet
    transactions_df = pd.DataFrame(all_transactions)
    transactions_df.to_parquet('transactions.parquet', index=False, compression='zstd')
    print(f"✓ transactions.parquet written ({len(transactions_df)} rows)")
    
    # Transaction inputs Parquet
    inputs_df = pd.DataFrame(all_inputs)
    inputs_df.to_parquet('transaction_inputs.parquet', index=False, compression='zstd')
    print(f"✓ transaction_inputs.parquet written ({len(inputs_df)} rows)")
    
    # Transaction outputs Parquet
    outputs_df = pd.DataFrame(all_outputs)
    outputs_df.to_parquet('transaction_outputs.parquet', index=False, compression='zstd')
    print(f"✓ transaction_outputs.parquet written ({len(outputs_df)} rows)")
    
    print("\nAll Parquet files have been successfully written to disk!")
    print("Note: Parquet files provide better compression and faster query performance with DuckDB.")

if __name__ == "__main__":
    main() 