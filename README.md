# Bitcoin Blockchain SQL Explorer

This project parses Bitcoin blockchain files and provides a powerful SQL interface for analysis using DuckDB and Parquet files for optimal performance.

## Features

- **High Performance**: Uses Parquet files for 2-4x better compression and faster queries
- **DuckDB Integration**: Native SQL querying with columnar storage optimization
- **Web Interface**: Beautiful web-based SQL explorer with real-time query execution
- **Blockchain Parsing**: Extracts block, transaction, input, and output data from blk.dat files
- **Progress Tracking**: Visual progress bars during parsing
- **Interactive Mode**: Command-line SQL interface for custom queries

## Performance Benefits

- **Parquet Format**: 2-4x smaller file sizes compared to CSV
- **Columnar Storage**: Only reads needed columns for queries
- **Compression**: ZSTD compression for optimal storage efficiency
- **Vectorized Processing**: DuckDB processes data in chunks for better memory usage
- **Predicate Pushdown**: Skips irrelevant data during queries

## Output Files

1. **blocks.parquet** - Block information including hash, size, timestamp, etc.
2. **transactions.parquet** - Transaction details including hash, size, fees, etc.
3. **transaction_inputs.parquet** - Input details for each transaction
4. **transaction_outputs.parquet** - Output details for each transaction

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

**Note**: The parser now generates Parquet files instead of CSV for better performance.

### Step 2: Set Up DuckDB Database

Set up DuckDB with the Parquet files:

```bash
python btc_duckdb_setup.py
```

This creates a `bitcoin_blockchain.db` file with all the data loaded and indexed.

### Step 3: Explore the Data

#### Option A: Web Interface (Recommended)
```bash
python btc_web_app.py
```
Then open http://localhost:5001 in your browser for a beautiful SQL interface.

#### Option B: Interactive Command Line
```bash
python btc_duckdb_setup.py
```
Choose interactive mode when prompted.

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

## Migration from CSV

If you have existing CSV files and want to migrate to Parquet for better performance:

```bash
python csv_to_parquet_migrator.py
```

This will convert your existing CSV files to Parquet format and show you the space savings.

## Example Queries

The web interface includes several example queries:

1. **Basic Stats** - Total blocks, first/last block numbers
2. **Transaction Stats** - Breakdown of coinbase vs regular transactions
3. **Daily Transaction Volume** - Transaction volume over time
4. **Largest Transactions** - Top transactions by value
5. **Recent Blocks** - Latest blocks in the chain
6. **Genesis Block** - Details about the first Bitcoin block

## Sample Results

From the sample data (119,964 blocks):
- **Total Blocks**: 119,964
- **Total Transactions**: 435,075
- **Coinbase Transactions**: 119,964
- **Regular Transactions**: 315,111
- **Total Output Value**: ~54,141,434 BTC

## Performance Comparison

| Format | File Size | Query Speed | Memory Usage |
|--------|-----------|-------------|--------------|
| CSV    | 100%      | Baseline    | High         |
| Parquet| 25-50%    | 5-10x faster| Low          |

## Dependencies

- `pandas` - For data manipulation
- `pyarrow` - For Parquet file support
- `tqdm` - For progress bars
- `python-bitcoinlib` - For Bitcoin protocol parsing
- `duckdb` - For SQL querying
- `flask` - For web interface

## Notes

- The script assumes mainnet magic bytes for the blk.dat file
- Some fields like script details and address decoding are simplified or skipped for performance
- Input values are set to 0 as they require UTXO lookup which is not implemented
- Parquet files provide significant performance improvements for analytical queries
- The web interface supports real-time query execution and result export 