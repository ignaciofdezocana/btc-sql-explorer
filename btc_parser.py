#!/usr/bin/env python3
"""
Bitcoin Blockchain Parser

This script reads a Bitcoin blockchain file (blk.dat) and converts it into four CSV files:
- blocks.csv: Block information
- transactions.csv: Transaction information  
- transaction_inputs.csv: Transaction input details
- transaction_outputs.csv: Transaction output details

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

# Bitcoin mainnet magic bytes
MAGIC_BYTES = b'\xf9\xbe\xb4\xd9'

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
                input_info = {
                    'transaction_hash': tx_hash.hex(),
                    'index': input_index,
                    'spent_transaction_hash': txin.prevout.hash.hex(),
                    'spent_output_index': txin.prevout.n,
                    'script_asm': '',  # Skip for now
                    'script_hex': txin.scriptSig.hex(),
                    'sequence': txin.nSequence,
                    'required_signatures': 0,  # Skip for now
                    'type': '',  # Skip for now
                    'addresses': [],  # Skip for now
                    'value': 0  # Skip for now
                }
                transaction_inputs.append(input_info)
            
            # Process outputs
            for output_index, txout in enumerate(tx.vout):
                output_info = {
                    'transaction_hash': tx_hash.hex(),
                    'index': output_index,
                    'script_asm': '',  # Skip for now
                    'script_hex': txout.scriptPubKey.hex(),
                    'required_signatures': 0,  # Skip for now
                    'type': '',  # Skip for now
                    'addresses': [],  # Skip for now
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
    
    # Create DataFrames and save to CSV
    print("\nWriting CSV files...")
    
    # Blocks CSV
    blocks_df = pd.DataFrame(all_blocks)
    blocks_df.to_csv('blocks.csv', index=False)
    print(f"✓ blocks.csv written ({len(blocks_df)} rows)")
    
    # Transactions CSV
    transactions_df = pd.DataFrame(all_transactions)
    transactions_df.to_csv('transactions.csv', index=False)
    print(f"✓ transactions.csv written ({len(transactions_df)} rows)")
    
    # Transaction inputs CSV
    inputs_df = pd.DataFrame(all_inputs)
    inputs_df.to_csv('transaction_inputs.csv', index=False)
    print(f"✓ transaction_inputs.csv written ({len(inputs_df)} rows)")
    
    # Transaction outputs CSV
    outputs_df = pd.DataFrame(all_outputs)
    outputs_df.to_csv('transaction_outputs.csv', index=False)
    print(f"✓ transaction_outputs.csv written ({len(outputs_df)} rows)")
    
    print("\nAll CSV files have been successfully written to disk!")

if __name__ == "__main__":
    main() 