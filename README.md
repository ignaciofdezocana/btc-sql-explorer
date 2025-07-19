# Bitcoin Blockchain Parser

This Python script reads a Bitcoin blockchain file (blk.dat) and converts it into four CSV files for analysis.

## Features

- Parses Bitcoin blockchain files using `python-bitcoinlib`
- Extracts block, transaction, input, and output data
- Outputs data to four separate CSV files
- Progress tracking with `tqdm`
- Handles mainnet Bitcoin blocks
- **NEW**: DuckDB integration for SQL querying

## Output Files

1. **blocks.csv** - Block information including hash, size, timestamp, etc.
2. **transactions.csv** - Transaction details including hash, size, fees, etc.
3. **transaction_inputs.csv** - Input details for each transaction
4. **transaction_outputs.csv** - Output details for each transaction

## Installation

1. Install the required dependencies:
```bash
pip install -r requirements.txt
```

## Usage

### Step 1: Parse the Blockchain File

Run the parser with the blockchain file path:

```bash
python btc_parser.py [blk_file_path]
```

If no file path is provided, it will default to `sample_blk.dat` in the current directory.

### Step 2: Set Up DuckDB Database

Set up DuckDB with the CSV files:

```bash
python btc_duckdb_setup.py
```

This creates a `bitcoin_blockchain.db` file with all the data loaded and indexed.

### Step 3: Run Queries

#### Option A: Run Predefined Queries
```bash
python run_queries.py
```

#### Option B: Interactive Query Tool
```bash
python btc_queries.py
```

#### Option C: Custom Queries
You can also run custom SQL queries directly:

```python
import duckdb

# Connect to the database
con = duckdb.connect('bitcoin_blockchain.db')

# Run a custom query
result = con.execute("""
    SELECT COUNT(*) as total_blocks 
    FROM blocks
""").fetchdf()

print(result)
```

## Example Queries

The predefined queries include:

1. **Blockchain Overview** - Total blocks, genesis block, latest block, etc.
2. **Transaction Analysis** - Transaction counts, coinbase vs regular transactions
3. **Block Size Analysis** - Distribution of block sizes
4. **Daily Transaction Volume** - Transaction volume over time
5. **Largest Transactions** - Top 10 largest transactions by value
6. **Blocks by Transaction Count** - Most active blocks
7. **Output Value Distribution** - Distribution of transaction output values
8. **Genesis Block Details** - Details about the first Bitcoin block

## Sample Results

From the sample data (119,964 blocks):
- **Total Blocks**: 119,964
- **Total Transactions**: 435,075
- **Coinbase Transactions**: 119,964
- **Regular Transactions**: 315,111
- **Total Output Value**: ~54,141,434 BTC

## Dependencies

- `pandas` - For CSV output
- `tqdm` - For progress bars
- `python-bitcoinlib` - For Bitcoin protocol parsing
- `duckdb` - For SQL querying

## Notes

- The script assumes mainnet magic bytes for the blk.dat file
- Some fields like script details and address decoding are simplified or skipped for performance
- Input values are set to 0 as they require UTXO lookup which is not implemented
- DuckDB provides fast analytical queries on the parsed data 