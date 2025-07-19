#!/usr/bin/env python3
"""
Bitcoin Blockchain DuckDB Setup

This script sets up DuckDB with the Bitcoin blockchain CSV files and provides example queries.
"""

import duckdb
import pandas as pd
import os

def setup_database():
    """Set up DuckDB database and load CSV files"""
    
    # Create a new DuckDB connection
    con = duckdb.connect('bitcoin_blockchain.db')
    
    print("Setting up DuckDB database...")
    
    # Drop existing tables if they exist
    print("Dropping existing tables if they exist...")
    con.execute("DROP TABLE IF EXISTS blocks")
    con.execute("DROP TABLE IF EXISTS transactions")
    con.execute("DROP TABLE IF EXISTS transaction_inputs")
    con.execute("DROP TABLE IF EXISTS transaction_outputs")
    
    # Create tables from CSV files
    print("Loading blocks.csv...")
    con.execute("""
        CREATE TABLE blocks AS 
        SELECT * FROM read_csv_auto('blocks.csv')
    """)
    
    print("Loading transactions.csv...")
    con.execute("""
        CREATE TABLE transactions AS 
        SELECT * FROM read_csv_auto('transactions.csv')
    """)
    
    print("Loading transaction_inputs.csv...")
    con.execute("""
        CREATE TABLE transaction_inputs AS 
        SELECT * FROM read_csv_auto('transaction_inputs.csv')
    """)
    
    print("Loading transaction_outputs.csv...")
    con.execute("""
        CREATE TABLE transaction_outputs AS 
        SELECT * FROM read_csv_auto('transaction_outputs.csv')
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
            AVG(output_value) as avg_output_value,
            SUM(output_value) as total_output_value
        FROM transactions
    """).fetchdf()
    print(result.to_string(index=False))
    
    # Query 3: Top 10 blocks by transaction count
    print("\n3. Top 10 Blocks by Transaction Count:")
    print("-" * 40)
    result = con.execute("""
        SELECT 
            number,
            hash,
            transaction_count,
            timestamp,
            size
        FROM blocks 
        ORDER BY transaction_count DESC 
        LIMIT 10
    """).fetchdf()
    print(result.to_string(index=False))
    
    # Query 4: Transaction volume over time (by day) - using epoch to date conversion
    print("\n4. Daily Transaction Volume (first 10 days):")
    print("-" * 40)
    result = con.execute("""
        SELECT 
            DATE(timestamp 'epoch' + block_timestamp * interval '1 second') as date,
            COUNT(*) as transaction_count,
            SUM(output_value) as total_volume
        FROM transactions 
        GROUP BY DATE(timestamp 'epoch' + block_timestamp * interval '1 second')
        ORDER BY date
        LIMIT 10
    """).fetchdf()
    print(result.to_string(index=False))
    
    # Query 5: Largest transactions
    print("\n5. Top 10 Largest Transactions:")
    print("-" * 40)
    result = con.execute("""
        SELECT 
            hash,
            output_value,
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
    # Check if CSV files exist
    required_files = ['blocks.csv', 'transactions.csv', 'transaction_inputs.csv', 'transaction_outputs.csv']
    missing_files = [f for f in required_files if not os.path.exists(f)]
    
    if missing_files:
        print(f"Error: Missing required CSV files: {missing_files}")
        print("Please run btc_parser.py first to generate the CSV files.")
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