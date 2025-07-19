#!/usr/bin/env python3
"""
Bitcoin Blockchain DuckDB Setup

This script sets up DuckDB with the Bitcoin blockchain Parquet files and provides example queries.
"""

import duckdb
import pandas as pd
import os

def setup_database():
    """Set up DuckDB database and load Parquet files"""
    
    # Create a new DuckDB connection
    con = duckdb.connect('bitcoin_blockchain.db')
    
    print("Setting up DuckDB database...")
    
    # Drop existing tables if they exist
    print("Dropping existing tables if they exist...")
    con.execute("DROP TABLE IF EXISTS blocks")
    con.execute("DROP TABLE IF EXISTS transactions")
    con.execute("DROP TABLE IF EXISTS transaction_inputs")
    con.execute("DROP TABLE IF EXISTS transaction_outputs")
    
    # Create tables from Parquet files with formatted timestamps
    print("Loading blocks.parquet with formatted timestamps...")
    con.execute("""
        CREATE TABLE blocks AS 
        SELECT 
            hash,
            number,
            strftime('%Y-%m-%d %H:%M:%S', timestamp 'epoch' + timestamp * interval '1 second') as timestamp,
            merkle_root,
            bits,
            nonce,
            version,
            weight,
            size,
            stripped_size,
            transaction_count,
            coinbase_param
        FROM read_parquet('blocks.parquet')
    """)
    
    print("Loading transactions.parquet with formatted timestamps...")
    con.execute("""
        CREATE TABLE transactions AS 
        SELECT 
            hash,
            block_hash,
            block_number,
            strftime('%Y-%m-%d %H:%M:%S', timestamp 'epoch' + block_timestamp * interval '1 second') as block_timestamp,
            is_coinbase,
            index,
            input_count,
            output_count,
            input_value,
            output_value,
            fee,
            size,
            virtual_size,
            version,
            lock_time
        FROM read_parquet('transactions.parquet')
    """)
    
    print("Loading transaction_inputs.parquet...")
    con.execute("""
        CREATE TABLE transaction_inputs AS 
        SELECT * FROM read_parquet('transaction_inputs.parquet')
    """)
    
    print("Loading transaction_outputs.parquet...")
    con.execute("""
        CREATE TABLE transaction_outputs AS 
        SELECT * FROM read_parquet('transaction_outputs.parquet')
    """)
    
    # Create indexes for better query performance
    print("Creating indexes...")
    con.execute("CREATE INDEX idx_blocks_hash ON blocks(hash)")
    con.execute("CREATE INDEX idx_blocks_number ON blocks(number)")
    con.execute("CREATE INDEX idx_transactions_hash ON transactions(hash)")
    con.execute("CREATE INDEX idx_transactions_block_hash ON transactions(block_hash)")
    con.execute("CREATE INDEX idx_inputs_tx_hash ON transaction_inputs(transaction_hash)")
    con.execute("CREATE INDEX idx_outputs_tx_hash ON transaction_outputs(transaction_hash)")
    
    print("Database setup complete!")
    return con

def run_example_queries(con):
    """Run some example queries to demonstrate the data"""
    
    print("\n" + "="*60)
    print("EXAMPLE QUERIES")
    print("="*60)
    
    # Query 1: Basic statistics
    print("\n1. Basic Blockchain Statistics:")
    print("-" * 40)
    result = con.execute("""
        SELECT 
            COUNT(*) as total_blocks,
            MIN(number) as first_block,
            MAX(number) as last_block,
            MIN(timestamp) as earliest_timestamp,
            MAX(timestamp) as latest_timestamp
        FROM blocks
    """).fetchdf()
    print(result.to_string(index=False))
    
    # Query 2: Transaction statistics
    print("\n2. Transaction Statistics:")
    print("-" * 40)
    result = con.execute("""
        SELECT 
            COUNT(*) as total_transactions,
            COUNT(CASE WHEN is_coinbase THEN 1 END) as coinbase_transactions,
            COUNT(CASE WHEN NOT is_coinbase THEN 1 END) as regular_transactions,
            ROUND(AVG(output_value / 100000000.0), 8) as avg_output_value_btc,
            ROUND(SUM(output_value / 100000000.0), 2) as total_output_value_btc
        FROM transactions
    """).fetchdf()
    print(result.to_string(index=False))
    
    # Query 3: Top 10 blocks by transaction count
    print("\n3. Top 10 Blocks by Transaction Count:")
    print("-" * 40)
    result = con.execute("""
        SELECT 
            number,
            SUBSTR(hash, 1, 16) || '...' as hash_short,
            transaction_count,
            timestamp,
            ROUND(size / 1024.0, 2) as size_kb
        FROM blocks 
        ORDER BY transaction_count DESC 
        LIMIT 10
    """).fetchdf()
    print(result.to_string(index=False))
    
    # Query 4: Transaction volume over time (by day)
    print("\n4. Daily Transaction Volume (first 10 days):")
    print("-" * 40)
    result = con.execute("""
        SELECT 
            DATE(block_timestamp) as date,
            COUNT(*) as transaction_count,
            ROUND(SUM(output_value / 100000000.0), 2) as total_volume_btc
        FROM transactions 
        GROUP BY DATE(block_timestamp)
        ORDER BY date
        LIMIT 10
    """).fetchdf()
    print(result.to_string(index=False))
    
    # Query 5: Largest transactions
    print("\n5. Top 10 Largest Transactions:")
    print("-" * 40)
    result = con.execute("""
        SELECT 
            SUBSTR(hash, 1, 16) || '...' as hash_short,
            ROUND(output_value / 100000000.0, 8) as output_value_btc,
            input_count,
            output_count,
            is_coinbase,
            block_number
        FROM transactions 
        ORDER BY output_value DESC 
        LIMIT 10
    """).fetchdf()
    print(result.to_string(index=False))
    
    # Query 6: Block size distribution
    print("\n6. Block Size Distribution:")
    print("-" * 40)
    result = con.execute("""
        SELECT 
            CASE 
                WHEN size < 1000 THEN '< 1KB'
                WHEN size < 10000 THEN '1-10KB'
                WHEN size < 100000 THEN '10-100KB'
                WHEN size < 1000000 THEN '100KB-1MB'
                ELSE '> 1MB'
            END as size_range,
            COUNT(*) as block_count,
            AVG(size) as avg_size
        FROM blocks 
        GROUP BY size_range
        ORDER BY MIN(size)
    """).fetchdf()
    print(result.to_string(index=False))

def interactive_mode(con):
    """Start interactive mode for custom queries"""
    print("\n" + "="*60)
    print("INTERACTIVE MODE")
    print("="*60)
    print("Enter SQL queries (type 'quit' to exit):")
    print("Available tables: blocks, transactions, transaction_inputs, transaction_outputs")
    
    while True:
        try:
            query = input("\nSQL> ").strip()
            if query.lower() in ['quit', 'exit', 'q']:
                break
            if not query:
                continue
            
            result = con.execute(query).fetchdf()
            if not result.empty:
                print(result.to_string(index=False))
            else:
                print("Query executed successfully (no results)")
                
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")

def main():
    # Check if Parquet files exist
    required_files = ['blocks.parquet', 'transactions.parquet', 'transaction_inputs.parquet', 'transaction_outputs.parquet']
    missing_files = [f for f in required_files if not os.path.exists(f)]
    
    if missing_files:
        print(f"Error: Missing required Parquet files: {missing_files}")
        print("Please run btc_parser.py first to generate the Parquet files.")
        return
    
    # Set up database
    con = setup_database()
    
    # Run example queries
    # run_example_queries(con)
    
    # Ask if user wants interactive mode
    response = input("\nWould you like to enter interactive mode for custom queries? (y/n): ")
    if response.lower() in ['y', 'yes']:
        interactive_mode(con)
    
    # Close connection
    con.close()
    print("\nDatabase connection closed. Database file: bitcoin_blockchain.db")

if __name__ == "__main__":
    main() 