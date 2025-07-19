# Bitcoin Blockchain SQL Explorer - Interactive UI

A simple, lightweight SQL interface for exploring Bitcoin blockchain data using tkinter.

## Features

- **SQL Editor**: Write and execute custom SQL queries
- **Example Queries**: Pre-built queries for common analysis tasks
- **Tabular Results**: Clean, scrollable table view of query results
- **Export to CSV**: Save query results to CSV files
- **Database Schema Viewer**: Browse table structures and column types
- **No Additional Dependencies**: Uses only built-in Python libraries (tkinter) and existing dependencies

## Prerequisites

1. **Database Setup**: Make sure you have run the database setup first:
   ```bash
   python btc_duckdb_setup.py
   ```

2. **Dependencies**: The UI uses the same dependencies as the main project:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

### Starting the UI

```bash
python btc_sql_ui.py
```

This will open a desktop application window with the SQL interface.

### Interface Layout

The UI is divided into three main sections:

1. **Left Panel - Example Queries**: 
   - List of pre-built example queries
   - Double-click or use "Load Query" button to load into editor

2. **Center - SQL Editor**:
   - Text area for writing SQL queries
   - "Execute Query" button to run queries
   - Query execution status and timing

3. **Right - Results Table**:
   - Tabular display of query results
   - Horizontal and vertical scrollbars
   - Auto-sized columns based on content

### Menu Options

- **File → Save Results**: Export current results to CSV
- **Query → Clear Editor**: Clear the SQL editor
- **Query → Show Table Schema**: View database table structures
- **Help → About**: Application information

### Example Queries Included

1. **Basic Stats**: Total blocks, first/last block numbers
2. **Transaction Stats**: Transaction counts by type
3. **Daily Transaction Volume**: Transaction volume over time
4. **Largest Transactions**: Top transactions by value
5. **Block Size Distribution**: Distribution of block sizes
6. **Recent Blocks**: Latest blocks in the chain
7. **Genesis Block**: Details about the first Bitcoin block
8. **Table Schemas**: Database structure information

### Writing Custom Queries

The SQL editor supports all standard SQL syntax and DuckDB-specific features:

```sql
-- Basic SELECT
SELECT * FROM blocks LIMIT 10;

-- Joins
SELECT b.number, b.hash, COUNT(t.hash) as tx_count
FROM blocks b
JOIN transactions t ON b.hash = t.block_hash
GROUP BY b.number, b.hash
ORDER BY b.number DESC
LIMIT 10;

-- Date functions
SELECT 
    DATE(timestamp 'epoch' + block_timestamp * interval '1 second') as date,
    COUNT(*) as tx_count
FROM transactions 
GROUP BY DATE(timestamp 'epoch' + block_timestamp * interval '1 second')
ORDER BY date;
```

### Available Tables

- **blocks**: Block information (hash, size, timestamp, etc.)
- **transactions**: Transaction details (hash, value, fees, etc.)
- **transaction_inputs**: Input details for each transaction
- **transaction_outputs**: Output details for each transaction

### Tips

1. **Performance**: Large queries may take time. The UI shows execution time and won't freeze during long queries.

2. **Results**: Results are displayed in a table format with auto-sized columns. Use scrollbars to navigate large result sets.

3. **Export**: Use "Save Results" to export query results to CSV for further analysis in Excel or other tools.

4. **Schema**: Use "Show Table Schema" to understand the database structure and available columns.

5. **Examples**: Start with example queries to understand the data structure and common query patterns.

## Troubleshooting

- **"Database not found"**: Run `python btc_duckdb_setup.py` first
- **"No database connection"**: Check that `bitcoin_blockchain.db` exists in the current directory
- **Query errors**: Check SQL syntax and table/column names using the schema viewer

## Advantages

- **Lightweight**: No web server or additional dependencies required
- **Fast**: Direct database connection with DuckDB
- **Simple**: Clean, intuitive interface
- **Portable**: Works on any system with Python and tkinter
- **Responsive**: Non-blocking UI with threaded query execution 