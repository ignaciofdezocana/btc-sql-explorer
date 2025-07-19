#!/usr/bin/env python3
"""
Bitcoin Blockchain SQL Explorer - Web Application

A modern, beautiful web-based SQL interface for exploring Bitcoin blockchain data.
"""

from flask import Flask, render_template, request, jsonify, send_file
import duckdb
import pandas as pd
import os
import json
from datetime import datetime
import io
import base64

app = Flask(__name__)

# Global database connection
db_connection = None

def get_db_connection():
    """Get database connection"""
    global db_connection
    if db_connection is None:
        if not os.path.exists('bitcoin_blockchain.db'):
            return None
        db_connection = duckdb.connect('bitcoin_blockchain.db')
        # Create saved_queries table if it doesn't exist
        ensure_saved_queries_table()
    return db_connection

def ensure_saved_queries_table():
    """Create saved_queries table if it doesn't exist"""
    global db_connection
    if db_connection is not None:
        try:
            # Check if table exists and has the right structure
            db_connection.execute("SELECT id, name, description, query, created_at, updated_at FROM saved_queries LIMIT 1")
        except Exception:
            # Table doesn't exist or has wrong structure, recreate it
            print("Recreating saved_queries table...")
            db_connection.execute("DROP TABLE IF EXISTS saved_queries")
            
        db_connection.execute("""
            CREATE TABLE IF NOT EXISTS saved_queries (
                id INTEGER PRIMARY KEY,
                name VARCHAR NOT NULL,
                description VARCHAR,
                query TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

@app.route('/')
def index():
    """Main page"""
    return render_template('index.html')

@app.route('/api/execute', methods=['POST'])
def execute_query():
    """Execute SQL query and return results"""
    try:
        data = request.get_json()
        query = data.get('query', '').strip()
        
        if not query:
            return jsonify({'error': 'No query provided'}), 400
        
        con = get_db_connection()
        if con is None:
            return jsonify({'error': 'Database not found. Please run btc_duckdb_setup.py first.'}), 500
        
        # Execute query
        start_time = datetime.now()
        result = con.execute(query).fetchdf()
        execution_time = (datetime.now() - start_time).total_seconds()
        
        # Convert to JSON-serializable format
        if result.empty:
            return jsonify({
                'success': True,
                'data': [],
                'columns': [],
                'row_count': 0,
                'column_count': 0,
                'execution_time': execution_time,
                'message': 'Query executed successfully (no results)'
            })
        
        # Convert DataFrame to list of dictionaries
        data_list = []
        for _, row in result.iterrows():
            row_dict = {}
            for col in result.columns:
                value = row[col]
                # Handle different data types
                if pd.isna(value):
                    row_dict[col] = None
                elif isinstance(value, (int, float)):
                    row_dict[col] = value
                else:
                    row_dict[col] = str(value)
            data_list.append(row_dict)
        
        return jsonify({
            'success': True,
            'data': data_list,
            'columns': list(result.columns),
            'row_count': len(result),
            'column_count': len(result.columns),
            'execution_time': execution_time,
            'message': f'Query executed successfully'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/schema')
def get_schema():
    """Get database schema"""
    try:
        con = get_db_connection()
        if con is None:
            return jsonify({'error': 'Database not found'}), 500
        
        schema_query = """
        SELECT 
            table_name,
            column_name,
            data_type
        FROM information_schema.columns 
        WHERE table_schema = 'main'
        ORDER BY table_name, ordinal_position
        """
        
        result = con.execute(schema_query).fetchdf()
        
        # Group by table
        schema = {}
        for _, row in result.iterrows():
            table_name = row['table_name']
            if table_name not in schema:
                schema[table_name] = []
            schema[table_name].append({
                'column': row['column_name'],
                'type': row['data_type']
            })
        
        return jsonify({'success': True, 'schema': schema})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stats')
def get_stats():
    """Get database statistics"""
    try:
        con = get_db_connection()
        if con is None:
            return jsonify({'error': 'Database not found'}), 500
        
        stats_query = """
        SELECT 
            (SELECT COUNT(*) FROM blocks) as total_blocks,
            (SELECT COUNT(*) FROM transactions) as total_transactions,
            (SELECT COUNT(*) FROM transaction_inputs) as total_inputs,
            (SELECT COUNT(*) FROM transaction_outputs) as total_outputs,
            (SELECT MIN(number) FROM blocks) as first_block,
            (SELECT MAX(number) FROM blocks) as last_block
        """
        
        result = con.execute(stats_query).fetchdf()
        stats = result.iloc[0]
        
        return jsonify({
            'success': True,
            'stats': {
                'total_blocks': int(stats['total_blocks']),
                'total_transactions': int(stats['total_transactions']),
                'total_inputs': int(stats['total_inputs']),
                'total_outputs': int(stats['total_outputs']),
                'first_block': int(stats['first_block']),
                'last_block': int(stats['last_block'])
            }
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export', methods=['POST'])
def export_results():
    """Export results to CSV"""
    try:
        data = request.get_json()
        results_data = data.get('data', [])
        filename = data.get('filename', 'query_results.csv')
        
        if not results_data:
            return jsonify({'error': 'No data to export'}), 400
        
        # Create DataFrame
        df = pd.DataFrame(results_data)
        
        # Create CSV in memory
        csv_buffer = io.StringIO()
        df.to_csv(csv_buffer, index=False)
        csv_buffer.seek(0)
        
        # Return CSV as downloadable file
        return send_file(
            io.BytesIO(csv_buffer.getvalue().encode('utf-8')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/examples')
def get_examples():
    """Get example queries"""
    examples = {
        "Basic Blockchain Stats": {
            "description": "Overall blockchain statistics and health metrics",
            "query": """
SELECT 
    COUNT(*) as total_blocks,
    MIN(number) as first_block,
    MAX(number) as last_block,
    SUM(transaction_count) as total_transactions,
    ROUND(AVG(size / 1024.0), 2) as avg_block_size_kb,
    ROUND(AVG(transaction_count), 1) as avg_tx_per_block
FROM blocks
WHERE number > 0"""
        },
        "Address Reuse Analysis": {
            "description": "Analyze Bitcoin address reuse patterns for privacy insights",
            "query": """
WITH address_usage AS (
    SELECT 
        unnest(addresses) as address,
        COUNT(DISTINCT transaction_hash) as tx_count,
        SUM(value) as total_received,
        COUNT(*) as output_count
    FROM transaction_outputs 
    WHERE addresses IS NOT NULL AND array_length(addresses, 1) > 0
    GROUP BY unnest(addresses)
),
usage_categories AS (
    SELECT 
        CASE 
            WHEN tx_count = 1 THEN 'One-time use'
            WHEN tx_count BETWEEN 2 AND 5 THEN 'Light reuse'
            WHEN tx_count BETWEEN 6 AND 20 THEN 'Moderate reuse'
            WHEN tx_count > 20 THEN 'Heavy reuse'
        END as usage_pattern,
        COUNT(*) as address_count,
        AVG(tx_count) as avg_transactions,
        SUM(total_received) as total_value
    FROM address_usage
    GROUP BY 1
)
SELECT 
    usage_pattern,
    address_count,
    ROUND(avg_transactions, 2) as avg_tx_per_address,
    ROUND(total_value / 100000000.0, 2) as total_btc,
    ROUND(100.0 * address_count / SUM(address_count) OVER(), 2) as percentage
FROM usage_categories 
ORDER BY address_count DESC"""
        },
        "Transaction Complexity Patterns": {
            "description": "Analyze transaction patterns and identify complex vs simple transactions",
            "query": """
WITH tx_patterns AS (
    SELECT 
        hash,
        input_count,
        output_count,
        ROUND(output_value / 100000000.0, 4) as output_btc,
        ROUND(fee / 100000000.0, 6) as fee_btc,
        size,
        CASE 
            WHEN input_count = 1 AND output_count = 1 THEN 'Simple (1:1)'
            WHEN input_count = 1 AND output_count = 2 THEN 'Payment + Change (1:2)'
            WHEN input_count > 1 AND output_count = 1 THEN 'Consolidation (N:1)'
            WHEN input_count = 1 AND output_count > 2 THEN 'Distribution (1:N)'
            WHEN input_count > 1 AND output_count > 1 THEN 'Complex (N:M)'
            ELSE 'Other'
        END as pattern_type
    FROM transactions 
    WHERE is_coinbase = false AND input_count > 0 AND output_count > 0
)
SELECT 
    pattern_type,
    COUNT(*) as transaction_count,
    ROUND(AVG(input_count + output_count), 1) as avg_total_ios,
    ROUND(AVG(output_btc), 4) as avg_value_btc,
    ROUND(AVG(fee_btc), 6) as avg_fee_btc,
    ROUND(AVG(size), 0) as avg_size_bytes,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER(), 2) as percentage
FROM tx_patterns 
GROUP BY pattern_type 
ORDER BY transaction_count DESC"""
        },
        "Daily Network Activity": {
            "description": "Daily transaction volume, fees, and network usage patterns",
            "query": """
WITH daily_stats AS (
    SELECT 
        DATE(timestamp 'epoch' + block_timestamp * interval '1 second') as date,
        COUNT(*) as tx_count,
        COUNT(CASE WHEN is_coinbase THEN 1 END) as coinbase_count,
        SUM(output_value) as total_output_value,
        SUM(fee) as total_fees,
        AVG(size) as avg_tx_size,
        SUM(input_count) as total_inputs,
        SUM(output_count) as total_outputs
    FROM transactions 
    GROUP BY DATE(timestamp 'epoch' + block_timestamp * interval '1 second')
)
SELECT 
    date,
    tx_count,
    tx_count - coinbase_count as regular_tx_count,
    ROUND(total_output_value / 100000000.0, 2) as total_volume_btc,
    ROUND(total_fees / 100000000.0, 4) as total_fees_btc,
    ROUND(avg_tx_size, 0) as avg_tx_size_bytes,
    ROUND(total_outputs::float / NULLIF(total_inputs, 0), 2) as output_input_ratio
FROM daily_stats 
WHERE date >= CURRENT_DATE - INTERVAL '14 days'
ORDER BY date DESC
LIMIT 14"""
        },
        "Fee Market Analysis": {
            "description": "Analyze transaction fee patterns and market dynamics",
            "query": """
WITH fee_stats AS (
    SELECT 
        DATE(timestamp 'epoch' + block_timestamp * interval '1 second') as date,
        COUNT(*) as tx_count,
        AVG(fee) as avg_fee,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY fee) as median_fee,
        PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY fee) as p90_fee,
        MIN(fee) as min_fee,
        MAX(fee) as max_fee,
        COUNT(CASE WHEN fee = 0 THEN 1 END) as zero_fee_count,
        AVG(size) as avg_size
    FROM transactions 
    WHERE is_coinbase = false AND fee IS NOT NULL
    GROUP BY DATE(timestamp 'epoch' + block_timestamp * interval '1 second')
)
SELECT 
    date,
    tx_count,
    ROUND(avg_fee / 100000000.0, 8) as avg_fee_btc,
    ROUND(median_fee / 100000000.0, 8) as median_fee_btc,
    ROUND(p90_fee / 100000000.0, 8) as p90_fee_btc,
    ROUND(avg_fee / avg_size, 2) as avg_fee_per_byte_sat,
    ROUND(100.0 * zero_fee_count / tx_count, 2) as zero_fee_percentage
FROM fee_stats 
WHERE date >= CURRENT_DATE - INTERVAL '10 days'
ORDER BY date DESC
LIMIT 10"""
        },
        "Whale Transaction Detection": {
            "description": "Identify large value transactions that could indicate whale activity",
            "query": """
WITH large_transactions AS (
    SELECT 
        hash,
        block_number,
        DATE(timestamp 'epoch' + block_timestamp * interval '1 second') as date,
        ROUND(output_value / 100000000.0, 2) as output_btc,
        input_count,
        output_count,
        ROUND(fee / 100000000.0, 4) as fee_btc,
        size,
        CASE 
            WHEN output_count = 1 THEN 'Consolidation'
            WHEN input_count = 1 THEN 'Distribution' 
            ELSE 'Complex'
        END as tx_pattern
    FROM transactions 
    WHERE output_value > 100000000000  -- More than 1000 BTC
      AND is_coinbase = false
)
SELECT 
    date,
    COUNT(*) as whale_tx_count,
    SUM(output_btc) as total_whale_volume_btc,
    AVG(output_btc) as avg_whale_size_btc,
    MAX(output_btc) as largest_tx_btc,
    SUM(fee_btc) as total_fees_paid_btc,
    ROUND(AVG(input_count), 1) as avg_inputs,
    ROUND(AVG(output_count), 1) as avg_outputs,
    COUNT(CASE WHEN tx_pattern = 'Consolidation' THEN 1 END) as consolidation_count,
    COUNT(CASE WHEN tx_pattern = 'Distribution' THEN 1 END) as distribution_count
FROM large_transactions 
GROUP BY date 
ORDER BY date DESC 
LIMIT 20"""
        },
        "Script Type Distribution": {
            "description": "Analyze the distribution of different Bitcoin script types",
            "query": """
WITH script_analysis AS (
    SELECT 
        type,
        COUNT(*) as output_count,
        SUM(value) as total_value,
        AVG(value) as avg_value,
        COUNT(CASE WHEN array_length(addresses, 1) > 0 THEN 1 END) as outputs_with_addresses
    FROM transaction_outputs 
    WHERE type IS NOT NULL
    GROUP BY type
)
SELECT 
    type,
    output_count,
    ROUND(total_value / 100000000.0, 2) as total_value_btc,
    ROUND(avg_value / 100000000.0, 6) as avg_value_btc,
    outputs_with_addresses,
    ROUND(100.0 * outputs_with_addresses / output_count, 2) as address_coverage_pct,
    ROUND(100.0 * output_count / SUM(output_count) OVER(), 2) as percentage_of_outputs
FROM script_analysis 
ORDER BY output_count DESC"""
        },
        "Block Mining Efficiency": {
            "description": "Analyze block mining timing and efficiency patterns",
            "query": """
WITH block_timing AS (
    SELECT 
        number,
        timestamp,
        size,
        transaction_count,
        LAG(timestamp) OVER (ORDER BY number) as prev_timestamp
    FROM blocks 
    WHERE number > 0
),
timing_analysis AS (
    SELECT 
        number,
        timestamp - prev_timestamp as block_interval_seconds,
        size,
        transaction_count,
        CASE 
            WHEN timestamp - prev_timestamp < 300 THEN 'Very Fast (<5min)'
            WHEN timestamp - prev_timestamp < 600 THEN 'Fast (5-10min)'
            WHEN timestamp - prev_timestamp < 900 THEN 'Normal (10-15min)'
            WHEN timestamp - prev_timestamp < 1800 THEN 'Slow (15-30min)'
            ELSE 'Very Slow (>30min)'
        END as timing_category
    FROM block_timing 
    WHERE prev_timestamp IS NOT NULL
)
SELECT 
    timing_category,
    COUNT(*) as block_count,
    ROUND(AVG(block_interval_seconds / 60.0), 2) as avg_interval_minutes,
    ROUND(MIN(block_interval_seconds / 60.0), 2) as fastest_minutes,
    ROUND(MAX(block_interval_seconds / 60.0), 2) as slowest_minutes,
    ROUND(AVG(size / 1024.0), 2) as avg_block_size_kb,
    ROUND(AVG(transaction_count), 1) as avg_tx_count,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER(), 2) as percentage
FROM timing_analysis 
GROUP BY timing_category 
ORDER BY 
    CASE timing_category 
        WHEN 'Very Fast (<5min)' THEN 1 
        WHEN 'Fast (5-10min)' THEN 2 
        WHEN 'Normal (10-15min)' THEN 3 
        WHEN 'Slow (15-30min)' THEN 4 
        ELSE 5 
    END"""
        }
    }
    
    return jsonify({'success': True, 'examples': examples})

@app.route('/api/saved-queries', methods=['GET'])
def get_saved_queries():
    """Get all saved queries"""
    try:
        con = get_db_connection()
        if con is None:
            return jsonify({'error': 'Database not found'}), 500
        
        result = con.execute("""
            SELECT id, name, description, query, created_at, updated_at
            FROM saved_queries
            ORDER BY updated_at DESC
        """).fetchdf()
        
        # Convert to list of dictionaries
        queries = []
        for _, row in result.iterrows():
            queries.append({
                'id': int(row['id']),
                'name': row['name'],
                'description': row['description'] if row['description'] else '',
                'query': row['query'],
                'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None
            })
        
        return jsonify({'success': True, 'queries': queries})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/saved-queries', methods=['POST'])
def save_query():
    """Save a new query"""
    try:
        data = request.get_json()
        name = data.get('name', '').strip()
        description = data.get('description', '').strip()
        query = data.get('query', '').strip()
        
        if not name:
            return jsonify({'error': 'Query name is required'}), 400
        if not query:
            return jsonify({'error': 'Query is required'}), 400
        
        con = get_db_connection()
        if con is None:
            return jsonify({'error': 'Database not found'}), 500
        
        # Check if name already exists
        existing = con.execute("""
            SELECT COUNT(*) as count FROM saved_queries WHERE name = ?
        """, [name]).fetchdf()
        
        if existing.iloc[0]['count'] > 0:
            return jsonify({'error': 'A query with this name already exists'}), 400
        
        # Get next ID
        max_id_result = con.execute("SELECT COALESCE(MAX(id), 0) + 1 as next_id FROM saved_queries").fetchdf()
        next_id = int(max_id_result.iloc[0]['next_id'])  # Convert to regular Python int
        
        # Insert new query
        con.execute("""
            INSERT INTO saved_queries (id, name, description, query, created_at, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, [next_id, name, description, query])
        
        return jsonify({
            'success': True, 
            'message': f'Query "{name}" saved successfully',
            'id': next_id
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/saved-queries/<int:query_id>', methods=['PUT'])
def update_saved_query(query_id):
    """Update an existing saved query"""
    try:
        data = request.get_json()
        name = data.get('name', '').strip()
        description = data.get('description', '').strip()
        query = data.get('query', '').strip()
        
        if not name:
            return jsonify({'error': 'Query name is required'}), 400
        if not query:
            return jsonify({'error': 'Query is required'}), 400
        
        con = get_db_connection()
        if con is None:
            return jsonify({'error': 'Database not found'}), 500
        
        # Check if query exists
        existing = con.execute("""
            SELECT COUNT(*) as count FROM saved_queries WHERE id = ?
        """, [query_id]).fetchdf()
        
        if existing.iloc[0]['count'] == 0:
            return jsonify({'error': 'Query not found'}), 404
        
        # Check if name is taken by another query
        name_check = con.execute("""
            SELECT COUNT(*) as count FROM saved_queries WHERE name = ? AND id != ?
        """, [name, query_id]).fetchdf()
        
        if name_check.iloc[0]['count'] > 0:
            return jsonify({'error': 'A query with this name already exists'}), 400
        
        # Update query
        con.execute("""
            UPDATE saved_queries 
            SET name = ?, description = ?, query = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, [name, description, query, query_id])
        
        return jsonify({
            'success': True, 
            'message': f'Query "{name}" updated successfully'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/saved-queries/<int:query_id>', methods=['DELETE'])
def delete_saved_query(query_id):
    """Delete a saved query"""
    try:
        con = get_db_connection()
        if con is None:
            return jsonify({'error': 'Database not found'}), 500
        
        # Get query name for response
        query_info = con.execute("""
            SELECT name FROM saved_queries WHERE id = ?
        """, [query_id]).fetchdf()
        
        if query_info.empty:
            return jsonify({'error': 'Query not found'}), 404
        
        query_name = query_info.iloc[0]['name']
        
        # Delete query
        con.execute("DELETE FROM saved_queries WHERE id = ?", [query_id])
        
        return jsonify({
            'success': True, 
            'message': f'Query "{query_name}" deleted successfully'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Check if database exists
    if not os.path.exists('bitcoin_blockchain.db'):
        print("❌ Database file 'bitcoin_blockchain.db' not found.")
        print("Please run btc_duckdb_setup.py first to create the database.")
        exit(1)
    
    print("🚀 Starting Bitcoin Blockchain SQL Explorer Web App...")
    print("📊 Database: bitcoin_blockchain.db")
    print("🌐 Web Interface: http://localhost:5001")
    print("Press Ctrl+C to stop the server")
    
    app.run(debug=True, host='0.0.0.0', port=5001) 